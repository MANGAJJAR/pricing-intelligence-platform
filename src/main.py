#main.py code file

from bs4 import BeautifulSoup
from langchain_text_splitters import HTMLSectionSplitter
import httpx
import re
import pandas as pd
import os, time
import json
import dotenv
from urllib.parse import urljoin
from json_repair import repair_json
import html
import asyncio
from openinference.instrumentation.langchain import LangChainInstrumentor
from openinference.instrumentation.anthropic import AnthropicInstrumentor
from tenacity import retry, stop_after_attempt, wait_exponential
from langchain_anthropic import ChatAnthropic
import math
import re
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Callable, Awaitable
import json_repair
import yaml
from math_chain4 import m_chain
from db_connection import TrinoConnection
from db_queries import dbQueryClient
from section_status import SectionProcessingStatus
import uuid
dotenv.load_dotenv("config/.env")
global controller
client_id = os.getenv("CLIENT_ID")
client_secret = os.getenv("CLIENT_SECRET")
token_url = os.getenv("token_url")
iliad_url = os.getenv("endpoint")

ndc_url = "https://dailymed.nlm.nih.gov/dailymed/search.cfm?labeltype=human&query="

limits = httpx.Limits(
    max_connections=50,
    max_keepalive_connections=10
)

log_dir = "log"
os.makedirs(log_dir, exist_ok=True)

# log filename with timestamp
log_file = os.path.join(log_dir, f"run_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s:%(lineno)d - [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Global variables for operation control
OPERATION_STATE = {
    "is_paused": False,
    "is_cancelled": False,
    "current_progress": 0,
    "total_items": 0,
    "job_id": None,
    "start_index": 0,  # For incremental processing
    "processed_ndcs": set()  # Track processed NDCs to avoid duplicates
}

class OperationController:
    """Controls the execution flow of NDC processing"""

    def __init__(self, job_id: str):
        self.job_id = job_id
        self.is_paused = False
        self.is_cancelled = False
        self.current_progress = 0
        self.total_items = 0
        self.start_index = 0
        self.processed_ndcs = set()
        self.unprocessed_links= set()

    async def check_operation_state(self):
        """Check if operation should pause or cancel"""
        while self.is_paused and not self.is_cancelled:
            await asyncio.sleep(1)  # Check every second

        if self.is_cancelled:
            raise asyncio.CancelledError("Operation was cancelled by user")

    def update_progress(self, current: int, total: int):
        """Update processing progress"""
        self.current_progress = current
        self.total_items = total

    def get_status(self) -> Dict:
        """Get current operation status"""
        return {
            "job_id": self.job_id,
            "is_paused": self.is_paused,
            "is_cancelled": self.is_cancelled,
            "progress": f"{self.current_progress}/{self.total_items}",
            "percentage": round((self.current_progress / self.total_items * 100), 2) if self.total_items > 0 else 0,
            "processed_ndcs": len(self.processed_ndcs)
        }

# Global controller instance
controller: Optional[OperationController] = None

def initialize_controller(job_id: str, start_index: int = 0, processed_ndcs: set = None, unprocessed_links: set =None):
    """Initialize the global controller"""
    global controller
    controller = OperationController(job_id)
    controller.start_index = start_index
    controller.processed_ndcs = processed_ndcs or set()
    controller.unprocessed_links = unprocessed_links or set()

def pause_operation():
    """Pause the current operation"""
    global controller
    if controller:
        controller.is_paused = True
        logger.info(f"[PAUSE] Operation paused for job: {controller.job_id}")

def resume_operation():
    """Resume the paused operation"""
    global controller
    if controller:
        controller.is_paused = False
        logger.info(f"[RESUME] Operation resumed for job: {controller.job_id}")

def cancel_operation():
    """Cancel the current operation"""
    global controller
    if controller:
        controller.is_cancelled = True
        logger.info(f"[CANCEL] Operation cancelled for job: {controller.job_id}")

def get_operation_status() -> Dict:
    """Get current operation status"""
    global controller
    if controller:
        return controller.get_status()
    return {"status": "No active operation"}


missed_ndcs =[]

err = {'NDC': [], 'Drug':[], 'error': [], 'input':[], 'output':[], 'function_name':[], 'status':[]}
err_ndcs = pd.DataFrame(data=err)
err_cnt = 0

user_llm_log = []  # tracking LLM input/output, failure per NDC
audit_log=[]

#CONFIG_JSON_PATH = "config/prompt_config.json"
CONFIG_JSON_PATH = "config/config.json"

@retry(stop=stop_after_attempt(50), wait=wait_exponential(multiplier=1, min=1, max=10))
async def get_chat_access_token():
    async with httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(30.0)) as client:
        response = await client.post(
            url=token_url,
            data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret
                  }
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            print(f"Auth request failed with status {e.response.status_code}: {e.response.text}")
            raise
        data = response.json()
        token = data["access_token"]
        return f"Bearer {token}"

def create_audit_record(function_name, ndc='', drug='', input_data='', output_data='', error='', prompt='', prompt_type='', citation=''):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = 'passed' if not error else 'failed'
    audit_record = {
        'timestamp': timestamp,
        'function_name': function_name,
        'NDC': ndc,
        'Drug': drug,
        'input': input_data,
        'output': output_data,
        'error': error,
        'status': status,
        'prompt': prompt,
        'prompt_type': prompt_type,
        'citation': citation
    }
    return audit_record

@retry(stop=stop_after_attempt(50), wait=wait_exponential(multiplier=2, min=1, max=20))
async def load_prompt(config_path, category_key, prompt_key):
    with open(config_path, 'r', encoding='utf-8') as config_file:
        config_data = json_repair.load(config_file)

    yaml_file_path = config_data[category_key][prompt_key]

    with open(yaml_file_path, 'r', encoding='utf-8') as yaml_stream:
        yaml_content = yaml.safe_load(yaml_stream)

    first_key = list(yaml_content.keys())[0]
    prompt_text = yaml_content[first_key]

    return prompt_text

@retry(stop=stop_after_attempt(50), wait=wait_exponential(multiplier=2, min=1, max=20))
async def scrape_article_details(ndc, article_url):
    try:
        async with httpx.AsyncClient(verify=False,limits=limits, timeout=httpx.Timeout(30.0)) as client:  #async with httpx.AsyncClient(follow_redirects=True) as client:
        #async with httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(30.0)) as client:  #async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(article_url)
            if response.status_code == 302:
                redirect_path = response.headers.get("Location")
                if not redirect_path:
                    print(f"Redirect without location for {ndc}")
                    user_llm_log.append({
                        'NDC': ndc,
                        'Drug': '',
                        'function_name': 'scrape_article_details',
                        'status': 'failed',
                        'input': article_url,
                        'output': '',
                        'error': 'Redirect without location header'
                    })
                    return None
                redirect_url = urljoin(article_url, redirect_path)
                print(f"Following redirect for NDC {ndc} → {redirect_url}")
                response = await client.get(redirect_url)
            response.raise_for_status()
            html = response.text
            soup = BeautifulSoup(html, "html.parser")
            label_tag = soup.find("span", id="drug-label")
            drug_label = label_tag.text.strip() if label_tag else "Unknown"
            # drug_label = soup.find("span", id="drug-label").text
            drug_info_section = soup.find('ul', class_='drug-information')
            packager_text = "Manufacturer is not present."

            if drug_info_section:
                for li in drug_info_section.find_all('li'):
                    if 'Packager:' in li.text:
                        packager_text = li.text.replace('Packager:', '').strip()
                        break

            drug_label_sections = soup.find("div", class_="drug-label-sections")
            user_llm_log.append({
                'NDC': ndc,
                'Drug': drug_label,
                'function_name': 'scrape_article_details',
                'status': 'passed' if drug_label != "Unknown" else 'failed',
                'input': article_url,
                'output': f"Drug: {drug_label}, Packager: {packager_text}",
                'error': '' if drug_label != "Unknown" else 'Drug label missing'
            })
            return drug_label_sections, drug_label, packager_text

    except Exception as e:
        logger.error(f"[FAILED] scrape_article_details | NDC: {ndc} | Error: {e}", exc_info=True)
        user_llm_log.append({
            'NDC': ndc,
            'Drug': '',
            'function_name': 'scrape_article_details',
            'status': 'failed',
            'input': article_url,
            'output': '',
            'error': str(e)
        })
        return None, "Unknown", "Manufacturer is not present."

@retry(stop=stop_after_attempt(50), wait=wait_exponential(multiplier=2, min=1, max=20))
async def extract_details2(prompt, sample_text="", image_url=None):
    logger.info("[START] extract_details2")
    AUTH_TOKEN = await get_chat_access_token()

    image_prompt = await load_prompt(CONFIG_JSON_PATH, 'image_details_prompt', 'path')

    messages = [
        {
            "role": "user",
            "content": []
        }
    ]

    if image_url:
        messages[0]["content"].append({
            "type": "image",
            "source": {
                "type": "url",
                "url": image_url
            }
        },
            {
            "type": "text",
            "text": image_prompt+sample_text
        })
        logger.info(f"[LLM CALL] extract_details2 with image\nURL: {image_url}\nTEXT:\n{prompt + sample_text}")
    else:
        messages[0]["content"].append({
            "type": "text",
            "text": prompt+sample_text
        })
        logger.info(f"[LLM CALL] extract_details2 without image\nTEXT:\n{prompt + sample_text}")
    iliad_url = "https://qa.api.abbvienet.com/iliad/abbvie"

    async with httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(30.0)) as client:
        response = await client.post(
            url=iliad_url + "/api/v1/chat/claude-3.5-sonnet",
            json={"messages": messages, "temperature": 0},
            headers={
                "Authorization": AUTH_TOKEN,
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            },
            timeout=20
        )

        response.raise_for_status()

        markup = response.json().get('completion', {}).get('content', 'No response content available.')
        logger.info(f"[LLM RESPONSE] extract_details2 OUTPUT:\n{markup}")
        return markup

@retry(stop=stop_after_attempt(50), wait=wait_exponential(multiplier=2, min=1, max=20))
async def extracting_indication_details(Sample_text, llm_anthropic, ndc="", drug=""):

    logger.info("[START] extracting_indication_details")
    logger.info(f"[LLM CALL] extracting_adverse_reaction_details | NDC: {ndc} | Drug: {drug}\nINPUT:\n{Sample_text}")
    audit_record = {
        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'function_name': 'extracting_indication_details',
        'NDC': ndc,
        'Drug': drug,
        'input': Sample_text,
        'output': '',
        'error': '',
        'status': 'pending',
        'prompt': '',
        'web_page_url':'',
        'prompt_type': 'extracting_indication_details_prompt',
        'citation': ''
    }
    audit_log.append(audit_record)
    try:
        prompt = await load_prompt(CONFIG_JSON_PATH, 'indication_details_prompt', 'path')

        messages = [
            {"role": "user",
            "content": [
                {
                    "type": "text",
                    "text": prompt + Sample_text
                }
            ]
            }
        ]

        response = await llm_anthropic.ainvoke(messages)
        # print(response.content)
        audit_record['prompt'] = prompt
        audit_record['output'] = response.content
        audit_record['status'] = 'passed'
        logger.info(f"[LLM RESPONSE] extracting_indication_details OUTPUT:\n{response.content}")
        user_llm_log.append({
            'NDC': ndc,
            'Drug': drug,
            'function_name': 'extracting_indication_details',
            'status': 'passed',
            'input': Sample_text,
            'output': response.content,
            'error': ''
        })
        return response.content

    except Exception as e:
        logger.error(f"[FAILED] extracting_indication_details | NDC: {ndc} | Drug: {drug} | Error: {e}", exc_info=True)
        audit_record['error'] = str(e)
        audit_record['status'] = 'failed'
        user_llm_log.append({
            'NDC': ndc,
            'Drug': drug,
            'function_name': 'extracting_indication_details',
            'status': 'fail',
            'input': Sample_text,
            'output': '',
            'error': str(e)
        })
        return ""

@retry(stop=stop_after_attempt(50), wait=wait_exponential(multiplier=2, min=1, max=20))
async def extracting_clinicalTrial_details(stringc, Clinical_text, llm_anthropic, ndc="", drug=""):
    logger.info("[START] extracting_clinicalTrial_details")
    logger.info(f"[LLM CALL] extracting_clinicalTrial_details INPUT:\n{Clinical_text}")
    prompt = await load_prompt(CONFIG_JSON_PATH, 'clinical_details_prompt', 'path').format(stringc=stringc)

    messages = [
            {"role": "user",
        "content": [
            {
                "type": "text",
                "text": prompt + Clinical_text
            }
        ]
        }
    ]

    response = await llm_anthropic.ainvoke(messages)
    # print(response.content)
    logger.info(f"[LLM RESPONSE] extracting_clinicalTrial_details OUTPUT:\n{response.content}")
    return response.content

@retry(stop=stop_after_attempt(50), wait=wait_exponential(multiplier=2, min=1, max=20))
async def extracting_adverse_reaction_details(stringc, additional_info, indication, short_form_of_disease, target_drug, adverse_reaction_text, llm_anthropic, ndc="", drug=""):

    logger.info("[START] extracting_adverse_reaction_details")
    logger.info(f"[LLM CALL] extracting_adverse_reaction_details INPUT:\n{adverse_reaction_text}")
    timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    audit_record = {
        'timestamp': timestamp,
        'function_name': 'extracting_adverse_reaction_details',
        'NDC': ndc,
        'Drug': drug,
        'input': adverse_reaction_text,
        'output': '',
        'error': '',
        'status': 'pending',
        'prompt': '',
        'web_page_url': '',
        'prompt_type': 'adverse_reaction_extraction_prompt',
        'citation': ''
    }
    audit_log.append(audit_record)
    try:
        prompt = await load_prompt(CONFIG_JSON_PATH, 'base_adverse_reaction_details_prompt', 'path')
        prompt = prompt.format(stringc=stringc,
							additional_info = additional_info,
                            indication = indication,
                            short_form_of_disease = short_form_of_disease,
                            drug_name = target_drug)
        # print(prompt)
        # repr(prompt)

        messages = [
                {"role": "user",
            "content": [
                {
                    "type": "text",
                    "text": prompt + adverse_reaction_text
                }
            ]
            }
        ]

        response= await llm_anthropic.ainvoke(messages)
        # print(response.content)
        logger.info(f"[LLM RESPONSE] extracting_adverse_reaction_details OUTPUT:\n{response.content}")
        user_llm_log.append({
            'NDC': ndc,
            'Drug': drug,
            'function_name': 'extracting_adverse_reaction_details',
            'status': 'passed',
            'input': adverse_reaction_text,
            'output': response.content,
            'error': ''
        })
        audit_record['prompt'] = prompt
        audit_record['output'] = response.content
        audit_record['status'] = 'passed'
        return response.content
    except Exception as e:
        logger.error(f"[FAILED] extracting_adverse_reaction_details | NDC: {ndc} | Drug: {drug} | Error: {e}", exc_info=True)
        user_llm_log.append({
            'NDC': ndc,
            'Drug': drug,
            'function_name': 'extracting_adverse_reaction_details',
            'status': 'failed',
            'input': adverse_reaction_text,
            'output': '',
            'error': str(e)
        })
        audit_record['error'] = str(e)
        audit_record['status'] = 'failed'
        return ""


async def scrape_clinical_and_indication_details(ndc, drug_label_sections, drug="",):
    try:
        sections = drug_label_sections.find_all("li")
        dosage_info =""

        for section in sections:
            section_title = section.find("a", id=lambda value: value and value.startswith("anch_dj_dj"))
            if not section_title or not section_title.text:
                continue

            section_title_text = section_title.text.upper()

            # ============================
            # INDICATIONS AND USAGE
            # ============================
            clean_section_title_text=html.unescape(section_title_text).upper()
            normalized_section_title_text = ' '.join(clean_section_title_text.split())
            pattern=r'\bINDICATIONS(?: AND USAGE)?\b'

            if re.search(pattern, normalized_section_title_text):
                # print(normalized_section_title_text)
                indication_usage_text = section.find("div", class_="Section toggle-content closed long-content")

                if len(indication_usage_text.text) > 5000:
                    header = indication_usage_text.find("h2")
                    if header:
                        sentence = "<html><body>" + str(indication_usage_text) + "</body></html>"
                        headers_to_split_on = [("h2", "Header 2")]
                        html_splitter = HTMLSectionSplitter(headers_to_split_on)
                        html_header_splits = html_splitter.split_text(sentence)
                        dosage_info = html_header_splits
                    else:
                        dosage_info = indication_usage_text.text
                else:
                    # print(indication_usage_text)
                    dosage_info = indication_usage_text.text
                    # print(dosage_info)
                logger.info("[SCRAPE] INDICATIONS AND USAGE section scraped")
                user_llm_log.append({
                    'NDC': ndc,
                    'Drug': drug,
                    'function_name': 'scrape_indication_usage',
                    'status': 'passed' if dosage_info else 'failed',
                    'input': 'drug_label_sections HTML',
                    'output': str(dosage_info)[:500],
                    'error': '' if dosage_info else 'Dosage info section empty'
                })
            # ============================
            # ADVERSE REACTIONS
            # ============================
            if "ADVERSE REACTIONS" in section_title_text:
                adverse_reactions_text = section.find("div", class_="Section toggle-content closed long-content" )
                all_adverse_reactions_content=adverse_reactions_text.text
                logger.info("[SCRAPE] ADVERSE REACTIONS section scraped")

                user_llm_log.append({
                    'NDC': ndc,
                    'Drug': drug,
                    'function_name': 'scrape_adverse_reactions',
                    'status': 'passed' if all_adverse_reactions_content else 'failed',
                    'input': 'drug_label_sections HTML',
                    'output': all_adverse_reactions_content[:500],
                    'error': '' if all_adverse_reactions_content else 'Adverse reactions section empty'
                })
                break

            else:
                None


    except Exception as e:
        print(f"Error during scraping: {e}")
        user_llm_log.append({
            'NDC': ndc,
            'Drug': drug,
            'function_name': 'scrape_clinical_sections',
            'status': 'failed',
            'input': 'drug_label_sections HTML',
            'output': '',
            'error': str(e)
        })

        None

    return dosage_info, all_adverse_reactions_content

async def Populating_Required_details(llm_anthropic,row=None,current_drug_url=None,ndc_url=None, update_section_status: Callable[[str], Awaitable[None]] = None):
    try:
        if row:
            raw_ndc = str(row).strip()
            parts = raw_ndc.split('-')
            if len(parts) == 3:
                NDCi = parts[0].zfill(5) + parts[1].zfill(4) + parts[2].zfill(2)
            else:
                NDCi = raw_ndc.replace('-', '').zfill(11)
            article_url = ndc_url + NDCi
        if current_drug_url:
            article_url = current_drug_url
        link_or_ndc = NDCi if row is not None else current_drug_url

        # if update_section_status:
        #     update_section_status.update_status(link_or_ndc, "Initializing")
        if update_section_status:
            await update_section_status(SectionProcessingStatus.STARTED.value, current_drug_url)

        logger.info(f"[START] Populating_Required_details | NDC: {link_or_ndc}")

    #try:

        # if status_tracker:
        #     status_tracker.update_status(link_or_ndc, "Scraping article details")
        article_details, label, packager = await scrape_article_details(ndc=link_or_ndc, article_url=article_url)

        # if status_tracker:
        #     status_tracker.update_status(link_or_ndc, "Scraping clinical sections")
        indication_details, adverse_reactions_details = await scrape_clinical_and_indication_details(link_or_ndc, article_details, drug=label)
        if not indication_details:
            logger.warning(f"[SCRAPE WARNING] Empty indication_details for NDC: {link_or_ndc}")
        if not adverse_reactions_details:
            logger.warning(f"[SCRAPE WARNING] Empty adverse_reactions_details for NDC: {link_or_ndc}")

        # --- Handle Indication ---
        # ============================
        # INDICATIONS AND USAGE
        # ============================

        if update_section_status:
            await update_section_status(SectionProcessingStatus.PROCESSING_INDICATION.value, current_drug_url)
        drug_rows = []

        # if status_tracker:
        #     status_tracker.update_status(link_or_ndc, "Processing Indication section")

        if isinstance(indication_details, str):
            logger.info(f"[LLM CALL] extracting_indication_details | NDC: {link_or_ndc} | Drug: {label}\nINPUT:\n{indication_details}")
            extracted = await extracting_indication_details(indication_details, llm_anthropic, ndc=link_or_ndc, drug=label)
            logger.info(f"[LLM RESPONSE] extracting_indication_details | NDC: {link_or_ndc} | Drug: {label}\nOUTPUT:\n{extracted}")
            soup = BeautifulSoup(extracted, 'html.parser')
            drug_rows.append({
                    "NDC": link_or_ndc,
                    "Drug_name": label,
                    "Manufacturer": packager,
                    "Parsed": soup.prettify()
                })
        elif isinstance(indication_details, list):
            tasks = []
            for ind in indication_details:
                logger.info(f"[LLM CALL] extracting_indication_details | NDC: {link_or_ndc} | Drug: {label}\nINPUT:\n{ind.page_content}")
                tasks.append(
                    extracting_indication_details(ind.page_content, llm_anthropic, ndc=link_or_ndc, drug=label)
                )
            results = await asyncio.gather(*tasks)
            for extracted in results:
                logger.info(f"[LLM RESPONSE] extracting_indication_details | NDC: {link_or_ndc} | Drug: {label}\nOUTPUT:\n{extracted}")
                soup = BeautifulSoup(extracted, 'html.parser')
                drug_rows.append({
                    "NDC": link_or_ndc,
                    "Drug_name": label,
                    "Manufacturer": packager,
                    "Parsed": soup.prettify()
                })

        # --- Handle Dosage ---
        # ============================
        # dosage administration
        # ============================

        #if status_tracker:
         #   status_tracker.update_status(link_or_ndc, "Processing Dosage section")

        dosage_text = await scrape_dosage_and_administration_details(link_or_ndc, article_details, drug=label)
        if not dosage_text:
            logger.warning(f"[SCRAPE WARNING] Empty dosage_text for NDC: {link_or_ndc}")
        logger.info(f"[LLM CALL] scrape_dosage_and_administration_details | NDC: {link_or_ndc} | Drug: {label}\nINPUT: N/A (HTML section used internally)\nOUTPUT:\n{dosage_text}")
        dosage_row = {
            "NDC": link_or_ndc,
            "Drug_name": label,
            "Parsed": dosage_text
        }

        # --- Handle Adverse Reactions ---
        # ============================
        # ADVERSE REACTIONS
        # ============================


        logger.info(f"[LLM CALL] extracting_adverse_reaction_details | NDC: {link_or_ndc} | Drug: {label}\nINPUT:\n{adverse_reactions_details}")

        #if status_tracker:
           # status_tracker.update_status(link_or_ndc, "Processing Adverse Reactions section")

        adverse_row = {
            "NDC": link_or_ndc,
            "Drug_name": label,
            "Parsed": adverse_reactions_details or ""
        }
        logger.info(f"[LLM RESPONSE] extracting_adverse_reaction_details | NDC: {link_or_ndc} | Drug: {label}\nOUTPUT:\n{adverse_reactions_details}")

        logger.info(f"[SUCCESS] Populating_Required_details completed for NDC: {link_or_ndc}")

        user_llm_log.append({
            'NDC': link_or_ndc,
            'Drug': label,
            'function_name': 'Populating_Required_details',
            'status': 'passed',
            'input': str(row),
            'output': f"Drug rows: {len(drug_rows)}, Dosage: {len(dosage_row)}, Adverse: {len(adverse_row)}",
            'error': ''
        })
        return drug_rows, dosage_row, adverse_row
    except Exception as e:
        logger.error(f"[FAILED] Populating_Required_details | NDC: {link_or_ndc} | Error: {e}", exc_info=True)

        # Tracking failed NDC in global error DataFrame
        err_ndcs.loc[len(err_ndcs)] = {'NDC': link_or_ndc, 'error': str(e)}
        user_llm_log.append({
            'NDC': link_or_ndc,
            'Drug': label if 'label' in locals() else 'Unknown',
            'function_name': 'Populating_Required_details',
            'status': 'failed',
            'input': str(row),
            'output': '',
            'error': str(e)
        })
        return None

    # finally:
    #     if status_tracker:
    #         status_tracker.clear_status(link_or_ndc)

def extraction_in_tabular_format(Drug_details):
    Final_Drug_details2=pd.DataFrame()
    for i in range(len(Drug_details)):
        try:
            article_indication = Drug_details.loc[i,'Parsed']
            Drug_name = Drug_details.loc[i,'Drug_name']
            if Drug_name == "Drug not found":
                Final_Drug_details2.loc[i] = pd.Series(dtype='float64')
            else:
                json_match = re.search(r'(\[.*\])', article_indication, re.DOTALL)
                print(f"Indication details regex match--- {json_match}")
                if json_match:

                    json_data = json_match.group(1) #group(1) the () content is considered irrespective of other text in re search

                    data = json.loads(json_data)
                    print(f"Indication details regex match data:{data}")

        # Convert JSON data to a pandas DataFrame
                df = pd.json_normalize(data)
                # print(f"df:{df}")
                df['NDC'] = Drug_details.loc[i,'NDC']
                df['Drug_name'] = Drug_details.loc[i,'Drug_name']
                df['Manufacturer']  = Drug_details.loc[i,'Manufacturer']

                if i == 0:
                    Final_Drug_details2 = df
                else:
                    Final_Drug_details2 = pd.concat([Final_Drug_details2, df], ignore_index=True)

        except Exception as e:
            print(f"An error occurred: {e}")

    print(f"Final_Drug_details3:{len(Final_Drug_details2)}")
    return Final_Drug_details2

@retry(stop=stop_after_attempt(50), wait=wait_exponential(multiplier=2, min=1, max=20))
async def scrape_dosage_and_administration_details(ndc,drug_label_sections, drug):
    try:
        section_divs = drug_label_sections.find_all("li")
        dosage_administration_info=''

        for section_div in section_divs:
            dosage_administration = section_div.find("a", id=lambda value: value and value.startswith("anch_dj_dj"))
            dosage_administration_text =""

            if dosage_administration:
                if dosage_administration.text:
                    dosage_administration1 = dosage_administration.text
                    if "DOSAGE AND ADMINISTRATION" in dosage_administration1:
                        dosage_administration_text = section_div.find("div", class_="Section toggle-content closed long-content" )

                        dosage_administration_info = dosage_administration_text.text
        #print(dosage_administration_info)
        user_llm_log.append({
            'NDC': ndc,
            'Drug': drug,  # fill in if label is available in calling context
            'function_name': 'scrape_dosage_and_administration_details',
            'status': 'passed' if dosage_administration_info else 'failed',
            'input': 'drug_label_sections HTML',
            'output': dosage_administration_info[:500],
            'error': '' if dosage_administration_info else 'Dosage section missing or empty'
        })
        logger.info(f"[SCRAPE] scrape_dosage_and_administration_details | NDC: {ndc} | Found: {'Yes' if dosage_administration_info else 'No'}")
        return dosage_administration_info

    except Exception as e:
        logger.error(f"[FAILED] scrape_dosage_and_administration_details | NDC: {ndc} | Error: {e}", exc_info=True)
        user_llm_log.append({
            'NDC': ndc,
            'Drug': drug,
            'function_name': 'scrape_dosage_and_administration_details',
            'status': 'failed',
            'input': 'drug_label_sections HTML',
            'output': '',
            'error': str(e)
        })
        return ''

@retry(stop=stop_after_attempt(50), wait=wait_exponential(multiplier=2, min=1, max=20))
async def extracting_dosage_details(Stringp, additional_info, indication, short_form_of_indication, dosage_text, llm_anthropic, ndc="", drug=""):
    print("Entered into extracting_dosage_details function")
    # AUTH_TOKEN = await get_chat_access_token()
    timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    audit_record = {
        'timestamp': timestamp,
        'function_name': 'extracting_dosage_details',
        'NDC': ndc,
        'Drug': drug,
        'input': dosage_text,
        'output': '',
        'error': '',
        'status': 'pending',
        'prompt': '',
        'web_page_url': '',
        'prompt_type': 'dosage_extraction_prompt',
        'citation': ''
    }
    audit_log.append(audit_record)
    try:
        prompt = await load_prompt(CONFIG_JSON_PATH, 'base_dosage_details_prompt', 'path')
        prompt = prompt.format(Stringp=Stringp,
								additional_info = additional_info,
                               indication = indication,
                               short_form_of_indication = short_form_of_indication)
        # print(prompt)
        # repr(prompt)
        messages = [
                {"role": "user",
            "content": [
                {
                    "type": "text",
                    "text": prompt + dosage_text
                }
            ]
            }
        ]
        #print(messages)
        response= await llm_anthropic.ainvoke(messages)
        # print(response.content)
        logger.info(f"[LLM RESPONSE] extracting_dosage_details | NDC: {ndc} | Drug: {drug}\nOUTPUT:\n{response.content}")
        user_llm_log.append({
            'NDC': ndc,
            'Drug': drug,
            'function_name': 'extracting_dosage_details',
            'status': 'passed',
            'input': dosage_text,
            'output': response.content,
            'error': ''
        })
        audit_record['prompt'] = prompt
        audit_record['output'] = response.content
        audit_record['status'] = 'passed'
        return response.content

    except Exception as e:
        logger.error(f"[FAILED] extracting_dosage_details | NDC: {ndc} | Drug: {drug} | Error: {e}", exc_info=True)
        user_llm_log.append({
            'NDC': ndc,
            'Drug': drug,
            'function_name': 'extracting_dosage_details',
            'status': 'fail',
            'input': dosage_text,
            'output': '',
            'error': str(e)
        })
        audit_record['error'] = str(e)
        audit_record['status'] = 'failed'
        return ""

def extract_dosage_administration_in_tabular_format(final_data):
    Final_dosage_administration_details = pd.DataFrame()
    all_rows = []

    if isinstance(final_data, str):
        print("D&A data is string")
        try:
            final_data = json.loads(final_data)
        except json.JSONDecodeError:
            print("Invalid JSON string")
    def safe_value(val):
        if isinstance(val, list):
            return ", ".join(map(str, val))
        return html.unescape(str(val)) if isinstance(val, str) else val
    for data in final_data:
        base_row = {}
        # if isinstance(data, dict):
        for key, value in data.items():
            if key != "Dosage_Scenario":
                base_row[key] = safe_value(value)

        scenarios = data.get("Dosage_Scenario", [])
        if isinstance(scenarios, dict):
            scenarios = [scenarios]

        if isinstance(scenarios, list) and len(scenarios) > 0:
            for scenario in scenarios:
                row = base_row.copy()
                # if isinstance(scenario, dict):
                for skey, svalue in scenario.items():
                    row[skey] = safe_value(svalue)
                all_rows.append(row)
        else:
            all_rows.append(base_row)

    print(all_rows)
    new_df = pd.DataFrame(all_rows)
    print(new_df)
    print(len(new_df))
    Final_dosage_administration_details = pd.concat([Final_dosage_administration_details, new_df], ignore_index=True)
    Final_dosage_administration_details_unique = Final_dosage_administration_details.drop_duplicates()
    # Final_dosage_administration_details_unique.to_excel("outputs/Final_dosage_administration_details_May15_25.xlsx")
    return Final_dosage_administration_details_unique

async def process_row_with_prompts(row, derived_prompt_keys,prompt_template_variables, llm_anthropic):
    derived_output = {}
    # derived_output= []
    format_vars = {key.replace(" ", "_"): row.get(key) for key in prompt_template_variables}
    print(format_vars)
    async def process_prompt(prompt_key):
        try:
            prompt_template = await load_prompt(CONFIG_JSON_PATH, prompt_key, 'path')
            # print(format_vars)
            prompt_filled = prompt_template.format(**format_vars)
            response = await m_chain(prompt_filled, llm_anthropic)
            # print(type(response))
            # print(response)
            if isinstance(response, list) and len(response) > 0 and isinstance(response[0], dict):

                parsed_row = {}
                for key, value in response[0].items():
                    if isinstance(value, str):
                        parsed_row[key] = html.unescape(str(value))
                    else:
                        parsed_row[key] = value
                # print(parsed_row)
                return parsed_row
                # return response
            else:
                return {}
        except Exception as e:
            return {}

    tasks = []
    for key in derived_prompt_keys:
        task = process_prompt(key)
        tasks.append(task)
    # print(tasks)
    prompt_results = await asyncio.gather(*tasks)

    for result in prompt_results:
        # derived_output.extend(result)
        derived_output.update(result)
        # print("Result:", result)
        # print("Type of result:", type(result))
    # print(derived_output)
    return derived_output

async def process_final_row(i, Final_Drug_details, dosage_administration_details,
                            all_adverse_reactions_details_df, error_log_file, failed_adverse_reactions_details, llm_anthropic,
							update_section_status: Callable[[str], Awaitable[None]] = None):
    try:
        Ndc = Final_Drug_details.loc[i, 'NDC']
        dosage = dosage_administration_details[dosage_administration_details["NDC"] == Ndc].reset_index()
        all_adverse_reactions_details = all_adverse_reactions_details_df[all_adverse_reactions_details_df["NDC"] == Ndc].reset_index()

        dosage_val = dosage.loc[0, 'Parsed']
        if not all_adverse_reactions_details.empty and 'Parsed' in all_adverse_reactions_details.columns:
            adverse_reactions_val = all_adverse_reactions_details.loc[0, 'Parsed']
        else:
            failed_adverse_reactions_details.append({"NDC": Ndc, "Parsed": None, "Index": i, "error": "Adverse reactions missing"})
            adverse_reactions_val = ""

        condition = Final_Drug_details.loc[i, 'Indication']
        subcondition = Final_Drug_details.loc[i, 'Subindications']
        Usage = Final_Drug_details.loc[i, 'Usage']
        Drug_name = Final_Drug_details.loc[i, 'Drug_name']
        Disease_full = Final_Drug_details.loc[i, 'Disease_full']
        Disease_short = Final_Drug_details.loc[i, 'Disease_short']
        LineofTherapy = Final_Drug_details.loc[i, 'LineofTherapy']
        TypeofTherapy = Final_Drug_details.loc[i, 'TypeofTherapy']
        Combinationdrug = Final_Drug_details.loc[i, 'Combinationdrug']

        #context = f"Drug name: {Drug_name} for disease_full:{Disease_full} for *Indication*: {condition} for subIndication:{subcondition} for usage:{Usage} for lineofTherapy:{LineofTherapy} for typeofTherapy:{TypeofTherapy} and for combinationdrug:{Combinationdrug}"

        short_form_of_disease = Disease_short
        short_form_of_indication = Disease_short

        #context = f"Drug name: {Drug_name} for disease_full:{Disease_full} for *Indication*: {condition} for subIndication:{subcondition} for usage:{Usage} for lineofTherapy:{LineofTherapy} for typeofTherapy:{TypeofTherapy} and for combinationdrug:{Combinationdrug}"
        context = (
            f"Drug Information:\n"
            f"- Drug Name: {Drug_name}\n"
            f"- Disease: {Disease_full} (*Indication*: {condition}, Sub-indication: {subcondition})\n"
            f"- short_form_of_indication: {short_form_of_indication}\n"
        )

        additional_info = (
            f"\nAdditional Information:\n"
            f"- Usage: {Usage}\n"
            f"- Line of Therapy: {LineofTherapy}\n"
            f"- Type of Therapy: {TypeofTherapy}\n"
            f"- Combination Drug: {Combinationdrug}"
        )
        dosage_json = await extracting_dosage_details(context, additional_info, condition, short_form_of_indication, str(dosage_val).strip(), llm_anthropic, ndc=Ndc,drug=Drug_name)
        adverse_json = await extracting_adverse_reaction_details(context, additional_info, condition, short_form_of_disease, Drug_name, str(adverse_reactions_val).strip(), llm_anthropic, ndc=Ndc,drug=Drug_name)

        dosage_match = re.search(r'(\[.*\])', dosage_json, re.DOTALL)
        if dosage_match:
            dosage_fixed = repair_json(dosage_match.group(1))
            dosage_data = json.loads(dosage_fixed)
            # print(dosage_data)
            df_dosage = extract_dosage_administration_in_tabular_format(dosage_data)

            ##### Processing derived columns #####
            derived_prompt_keys = ['derived_yearly_dosage_prompt','derived_subsequent_year_dosage_prompt','derived_first_year_dosage_prompt']
            dosage_prompt_template_variables = ["Recommended Dosage", "Cycle Duration", "Duration of Treatment"]
            tasks = []
            for index, row in df_dosage.iterrows():
                task = process_row_with_prompts(row, derived_prompt_keys, dosage_prompt_template_variables, llm_anthropic)
                tasks.append(task)
            derived_results = await asyncio.gather(*tasks)
            # print(derived_results)
            # Merging derived columns back
            df_derived = pd.DataFrame(derived_results)
            print(df_derived)
            df_dosage = pd.concat([df_dosage.reset_index(drop=True), df_derived.reset_index(drop=True)], axis=1)

        else:
            df_dosage = pd.DataFrame()

        adverse_reaction_data = []
        adverse_match = re.search(r'(\[.*\])', adverse_json, re.DOTALL)
        if adverse_match:
            try:
                adverse_fixed = repair_json(adverse_match.group(1))
                adverse_reaction_data = json.loads(adverse_fixed)
                # print(adverse_reaction_data)

                if isinstance(adverse_reaction_data, dict):
                    adverse_reaction_data = [adverse_reaction_data]
            except json.JSONDecodeError:
                matches = re.findall(r'\[.*?\]', adverse_json, re.DOTALL)
                for match in matches:
                    try:
                        fixed = repair_json(match)
                        adverse_reaction_data.extend(json.loads(fixed))
                    except Exception as inner_e:
                        error_log_file.append({"NDC": Ndc, "Function": "fallback adverse regex", "Indication Index": i, "error": str(inner_e)})
                        continue

        if adverse_reaction_data:
            df_adverse = pd.json_normalize(adverse_reaction_data)
            # print(f"df_adverse_normalized:{df_adverse}")
            adverse_reaction_derived_prompt_keys = ['derived_adverse_reaction_weeks_prompt']
            adverse_reaction_prompt_template_variables = ['Median duration of treatment', 'Max duration of treatment']
            adverse_reaction_tasks = []
            for index, row in df_adverse.iterrows():
                adverse_reaction_task = process_row_with_prompts(row, adverse_reaction_derived_prompt_keys, adverse_reaction_prompt_template_variables, llm_anthropic)
                adverse_reaction_tasks.append(adverse_reaction_task)
            derived_results_adverse_reaction = await asyncio.gather(*adverse_reaction_tasks)
            # print(derived_results_adverse_reaction)
            # Merging derived columns back
            df_derived_adverse_reaction = pd.DataFrame(derived_results_adverse_reaction)
            df_adverse=df_adverse.add_prefix('adverse_reaction_')
            df_derived_adverse_reaction=df_derived_adverse_reaction.add_prefix('adverse_reaction_')
            df_adverse = pd.concat([df_adverse.reset_index(drop=True), df_derived_adverse_reaction.reset_index(drop=True)], axis=1)

            # df_adverse = df_adverse[
            #     ~((df_adverse['adverse_reaction_study_id'].str.strip() == 'N/A') &
            #         (df_adverse['adverse_reaction_Median duration of treatment'].str.strip() == 'N/A'))
            # ]
            df_adverse = df_adverse[
                ~((df_adverse['adverse_reaction_Max duration of treatment'].str.strip() == 'N/A') &
                    (df_adverse['adverse_reaction_Median duration of treatment'].str.strip() == 'N/A'))
            ]
            if df_adverse.empty:
                # Keeping the DataFrame structure with column headers even if no rows
                df_adverse = pd.DataFrame(columns=df_adverse.columns)

            if df_adverse is not None and not df_adverse.empty:
                # Converting durations to numeric, replacing "N/A" or blanks with NaN
                df_adverse["adverse_reaction_Median duration of treatment (weeks)"] = pd.to_numeric(
                    df_adverse["adverse_reaction_Median duration of treatment (weeks)"], errors="coerce"
                )
                df_adverse["adverse_reaction_Max duration of treatment (weeks)"] = pd.to_numeric(
                    df_adverse["adverse_reaction_Max duration of treatment (weeks)"], errors="coerce"
                )

                # Creating a new column to compare
                df_adverse["duration_score"] = df_adverse[[
                    "adverse_reaction_Median duration of treatment (weeks)",
                    "adverse_reaction_Max duration of treatment (weeks)"
                ]].max(axis=1)

                # Picking the row with max duration_score
                # if not df_adverse.empty:
                df_adverse = df_adverse.sort_values(by="duration_score", ascending=False).head(1)

                # Droping helper column
                df_adverse.drop(columns=["duration_score"], inplace=True)

                print(f"df_adverse_after_removing_NA:{df_adverse}")
                # print(type(df_dosage))

                df_combined = df_dosage.merge(df_adverse, how="cross")
            else:
                df_combined = df_dosage.copy()
            # print(f"df_adverse_after_removing_NA:{df_adverse}")
            # if df_adverse is not None and not df_adverse.empty:
            #     print(type(df_dosage))

            #     df_combined = df_dosage.merge(df_adverse, how="cross")
            # else:
            #     df_combined = df_dosage.copy()

        else:
            df_combined = df_dosage

        row_dict = Final_Drug_details.iloc[i].to_dict()

        colindex = int(0)
        for col, val in row_dict.items():
            #df[col] = str(val)
            repeated_value = [str(val)] * len(df_combined)
            df_combined.insert(loc = colindex, column = col, value = repeated_value)
            colindex = colindex + 1

        user_llm_log.append({
            'NDC': Final_Drug_details.loc[i, 'NDC'],
            'Drug': Final_Drug_details.loc[i, 'Drug_name'],
            'function_name': 'process_final_row',
            'status': 'passed',
            'input': context,
            'output': f"Dosage + Adverse JSON generated successfully",
            'error': ''
        })
        return df_combined

    except Exception as e:
        error_log_file.append({"NDC": Final_Drug_details.loc[i, 'NDC'], "Function": "process_final_row", "Indication Index": i, "error": str(e)})
        user_llm_log.append({
            'NDC': Final_Drug_details.loc[i, 'NDC'],
            'Drug': Final_Drug_details.loc[i, 'Drug_name'],
            'function_name': 'process_final_row',
            'status': 'failed',
            'input': context if 'context' in locals() else '',
            'output': '',
            'error': str(e)
        })
        return None

async def ndcs_pause_info(job_id: str, processed_ndcs: set, current_index: int,
                         all_final_drug_details: pd.DataFrame,
                         all_final_combined_details: pd.DataFrame):
    pause_ndc_data = {
        "job_id": job_id,
        "timestamp": datetime.now().isoformat(),
        "processed_ndcs": list(processed_ndcs),
        "current_index": current_index,
        "total_processed": len(processed_ndcs)
    }
    return pause_ndc_data

async def save_checkpoint(job_id: str, processed_ndcs: set, current_index: int,
                         all_final_drug_details: pd.DataFrame,
                         all_final_combined_details: pd.DataFrame,processed_dicts:list):
    """Save processing checkpoint for resume functionality"""
    checkpoint_data = {
        "job_id": job_id,
        "timestamp": datetime.now().isoformat(),
        "processed_ndcs": list(processed_ndcs),
        "current_index": current_index,
        "total_processed": len(processed_ndcs),
        "processed_dicts": processed_dicts
    }

    checkpoint_path = f"checkpoints/checkpoint_{job_id}.json"
    os.makedirs("checkpoints", exist_ok=True)

    with open(checkpoint_path, 'w') as f:
        json.dump(checkpoint_data, f, indent=2)

    # Save intermediate data
    if not all_final_drug_details.empty:
        all_final_drug_details.to_excel(f"checkpoints/drug_details_{job_id}.xlsx", index=False)
    if not all_final_combined_details.empty:
        all_final_combined_details.to_excel(f"checkpoints/combined_details_{job_id}.xlsx", index=False)

    logger.info(f"[CHECKPOINT] Saved checkpoint for job: {job_id}")

async def load_checkpoint(job_id: str) -> Dict:
    """Load processing checkpoint for resume functionality"""
    checkpoint_path = f"checkpoints/checkpoint_{job_id}.json"

    if not os.path.exists(checkpoint_path):
        return None

    try:
        with open(checkpoint_path, 'r') as f:
            checkpoint_data = json.load(f)

        # Load intermediate data if exists
        drug_details_path = f"checkpoints/drug_details_{job_id}.xlsx"
        combined_details_path = f"checkpoints/combined_details_{job_id}.xlsx"

        all_final_drug_details = pd.DataFrame()
        all_final_combined_details = pd.DataFrame()

        if os.path.exists(drug_details_path):
            all_final_drug_details = pd.read_excel(drug_details_path)
        if os.path.exists(combined_details_path):
            all_final_combined_details = pd.read_excel(combined_details_path)

        checkpoint_data['all_final_drug_details'] = all_final_drug_details
        checkpoint_data['all_final_combined_details'] = all_final_combined_details

        logger.info(f"[CHECKPOINT] Loaded checkpoint for job: {job_id}")
        return checkpoint_data

    except Exception as e:
        logger.error(f"[CHECKPOINT] Error loading checkpoint: {e}")
        return None

@retry(stop=stop_after_attempt(50), wait=wait_exponential(multiplier=2, min=1, max=20))
async def load_column_names(config_path, category_key):
    with open(config_path, 'r', encoding='utf-8') as config_file:
        config_data = json_repair.load(config_file)

    trino_column_names = config_data[category_key]
    print(trino_column_names)
    return trino_column_names

# now=datetime.now().strftime('%Y-%m-%d %H:%M:%S') #check
now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')  # string in UTC

async def main(job_id: str = None, input_file: str = None, start_index: int = 0,
               processed_ndcs: set = None, resume_data: Dict = None, list_links: list[dict] =None,
			                  update_section_status: Callable[[str], Awaitable[None]] = None, user_id:str=None, unprocessed_links : set=None):
    """
    Enhanced main function with operation controls

    Args:
        job_id: Unique job identifier
        input_file: Path to input Excel file
        start_index: Starting index for processing (for incremental runs)
        processed_ndcs: Set of already processed NDCs (for resume)
        resume_data: Data from checkpoint for resume functionality
    """
    global controller

    # Initialize controller
    logger.info(f"[DEBUG] input_file type: {type(input_file)}, value: {input_file}")
    logger.info(f"[DEBUG] list_links type: {type(list_links)}, length: {len(list_links) if list_links else 0}")

    initialize_controller(job_id or "default", start_index, processed_ndcs, unprocessed_links)
    NDCParsed=None
    #if status_tracker:
        #status_tracker.job_id = job_id

    try:
        LangChainInstrumentor().instrument()
        AnthropicInstrumentor().instrument()

        start_time = time.perf_counter()

        # Use provided input file or default
        end_index2=0
        #input_file = input_file or "inputs/test_input_4NDC_eyecare.xlsx"
        ndc_url = "https://dailymed.nlm.nih.gov/dailymed/search.cfm?labeltype=human&query="

        if input_file and input_file is not None:
            input_file = input_file
            df = pd.read_excel(input_file)
            NDCParsed = df[['NDC']].drop_duplicates(subset='NDC').dropna().reset_index(drop=True)
            print(f"Processing {len(NDCParsed)} NDCs starting from index {start_index}")
            controller.update_progress(start_index, len(NDCParsed))
            end_index2=len(NDCParsed)

        if list_links:
            controller.update_progress(start_index, len(list_links))
            end_index2=len(list_links)
            from typing import List, Dict
            all_urls = [d["Drug_URL"] for d in list_links]
            # unprocessed_links = [url for url in all_urls if url not in processed_links]
            # return unprocessed_links


        # Initialize tracking variables
        error_log_file = []
        failed_adverse_reactions_details = []

        # Load from resume data if available
        if resume_data:
            All_Final_Drug_details2 = resume_data.get('all_final_drug_details', pd.DataFrame())
            All_Final_combined_details = resume_data.get('all_final_combined_details', pd.DataFrame())
            start_index = resume_data.get('current_index', 0)
            controller.processed_ndcs = set(resume_data.get('processed_ndcs', []))
            unprocessed_links = [url for url in all_urls if url not in controller.processed_ndcs]
            controller.unprocessed_links = set(unprocessed_links)
        else:
            All_Final_Drug_details2 = pd.DataFrame()
            All_Final_combined_details = pd.DataFrame()
            all_mapping_df = pd.DataFrame()

        print(f"Processing {end_index2} NDCs or URLs starting from index {start_index}")
        controller.update_progress(start_index, end_index2)

        AUTH_TOKEN = await get_chat_access_token()
        iliad_url = "https://qa.api.abbvienet.com/iliad/abbvie"

        llm_anthropic = ChatAnthropic(
                # base_url="https://prod.api.abbvienet.com/iliad/abbvie/anthropic",
                # base_url="https://qa.api.abbvienet.com/iliad/abbvie/anthropic",
                base_url = iliad_url + "/anthropic",
                model="claude-3-7-sonnet-20250219",
                api_key=f"{AUTH_TOKEN}",
                default_headers={"Authorization": f"{AUTH_TOKEN}","anthropic-beta":"tools-2024-04-04"},
                model_kwargs={"extra_headers": {"anthropic-beta": "prompt-caching-2024-07-31"}},
                temperature=0.0,
                max_tokens=4096
            )

        batch_size = 20

        # MAIN PROCESSING LOOP WITH CONTROLS
        processed_link_dict=[]
        for i in range(start_index, end_index2):

            #current_ndc = NDCParsed.iloc[i]['NDC']

            # if status_tracker:
            #     status_tracker.update_status(current_ndc, "Initializing")

            # CHECK OPERATION STATE BEFORE EACH ITERATION
            await controller.check_operation_state()

            # Skip if already processed (for resume functionality)
            #if current_ndc in controller.processed_ndcs:
                #if status_tracker:
                    #status_tracker.clear_status(current_ndc)
                #print(f"Skipping already processed NDC: {current_ndc}")
                #continue

            drug_rows = []
            dosage_rows = []
            adverse_rows = []

            #print(f"Processing NDC {i+1}/{len(NDCParsed)}: {current_ndc}")
            #controller.update_progress(i + 1, len(NDCParsed))


            processed_id=""
            try:
                # Your existing processing logic
                print(input_file)
                print(type(input_file))
                # current_ndc = NDCParsed.iloc[i]['NDC']
                if input_file and input_file is not None:
                    current_ndc = NDCParsed.iloc[i]['NDC']
                    if current_ndc in controller.processed_ndcs:
                        print(f"Skipping already processed NDC: {current_ndc}")
                        continue
                    print(f"Processing NDC {i+1}/{len(NDCParsed)}: {current_ndc}")
                    controller.update_progress(i + 1, len(NDCParsed))
                    Drug_details_list, dosage_administration_details_dict, all_adverse_reactions_details_dict = await Populating_Required_details(
                                                                                row=current_ndc, ndc_url=ndc_url, llm_anthropic=llm_anthropic,
                                                                                update_section_status=update_section_status)

                    processed_id = current_ndc
                if list_links:
                    current_drug_url = list_links[i]["Drug_URL"]
                    if current_drug_url in controller.processed_ndcs:
                        print(f"Skipping already processed NDC: {current_drug_url}")
                        continue
                    print(f"Processing NDC {i+1}/{len(list_links)}: {current_drug_url}")
                    controller.update_progress(i + 1, len(list_links))
                    # Your existing processing logic
                    Drug_details_list, dosage_administration_details_dict, all_adverse_reactions_details_dict = await Populating_Required_details(
                        current_drug_url= current_drug_url, ndc_url=ndc_url, llm_anthropic=llm_anthropic, update_section_status=update_section_status
                    )
                    processed_id = current_drug_url
                #Drug_details_list, dosage_administration_details_dict, all_adverse_reactions_details_dict = await Populating_Required_details(
                 #   current_ndc, ndc_url, llm_anthropic, status_tracker
                #)

                drug_rows.extend(Drug_details_list)
                dosage_rows.append(dosage_administration_details_dict)
                adverse_rows.append(all_adverse_reactions_details_dict)

                Drug_details = pd.DataFrame(drug_rows)
                dosage_administration_details = pd.DataFrame(dosage_rows)
                all_adverse_reactions_details_df = pd.DataFrame(adverse_rows)

                print(f"len of drug details before regex match: {len(Drug_details)}")
                Final_Drug_details = extraction_in_tabular_format(Drug_details)

                if Final_Drug_details is not None and not Final_Drug_details.empty:
                    Final_Drug_details.dropna(inplace=True)
                else:
                    print("Final_Drug_details is None or empty.")
                    continue

                # Concatenate results
                if All_Final_Drug_details2.empty:
                    All_Final_Drug_details2 = Final_Drug_details
                else:
                    All_Final_Drug_details2 = pd.concat([All_Final_Drug_details2, Final_Drug_details], ignore_index=True)

                Final_Drug_details = Final_Drug_details.reset_index(drop=True)
                n = 3
                last_cols = Final_Drug_details.iloc[:, -n:]
                other_cols = Final_Drug_details.iloc[:, :-n]
                Final_Drug_details = pd.concat([last_cols, other_cols], axis=1)

                # Process final rows in batches
				# Add here - both sections
                if update_section_status:
                    await update_section_status(SectionProcessingStatus.PROCESSING_DOSAGE_AND_ADVERSE_REACTIONS.value, current_drug_url)

                tasks = []
                for j in range(len(Final_Drug_details)):
                    task = process_final_row(
                        j,
                        Final_Drug_details,
                        dosage_administration_details,
                        all_adverse_reactions_details_df,
                        error_log_file,
                        failed_adverse_reactions_details,
                        llm_anthropic
                    )
                    tasks.append(task)

                batch_count = math.ceil(len(tasks) / batch_size)
                valid_results = []

                for k in range(batch_count):
                    # CHECK OPERATION STATE BEFORE EACH BATCH
                    await controller.check_operation_state()

                    batch = tasks[k * batch_size: (k + 1) * batch_size]
                    results = await asyncio.gather(*batch)
                    batch_valid = [r for r in results if r is not None]
                    valid_results.extend(batch_valid)

                if valid_results:
                    Final_combined_details = pd.concat(valid_results, ignore_index=True)

                    Final_combined_details["Derived duration of treatment"] = Final_combined_details.apply(
                        lambda row: row.get('adverse_reaction_Max duration of treatment (weeks)', 'N/A')
                        if row.get('Max Duration (Weeks)', 'N/A') == 'N/A'
                        else row.get('Max Duration (Weeks)', 'N/A'),
                        axis=1
                    )
                    Final_combined_details["Derived Set Duration Under 1 Year"] = Final_combined_details.apply(
                        lambda row: "No" if row['Derived duration of treatment'] == 'N/A' or
                        pd.to_numeric(row['Derived duration of treatment'], errors='coerce') > 52 else "Yes",
                        axis=1
                    )
                    Final_combined_details.columns=Final_combined_details.columns.str.lower().str.replace(' ', '_')
                    trino_schema="z_abv_cws_bts_ps_data_analytics"
                    trino_table="dailymed_dosing_raw"
                    if list_links:
                        Final_combined_details.rename(columns={'ndc': 'drug_url'}, inplace=True)
                        logger.info(f"[DEBUG] Renamed NDC to drug_url")

                        try:
                            unique_links = Final_combined_details['drug_url'].dropna().unique().tolist()
                            # drug_url = list_links[i]["Drug_URL"]
                            conn=TrinoConnection()
                            logger.info(f"[DEBUG] Connection instance type: {type(conn)}")
                            insert_into_trino=dbQueryClient(conn)
                            # checking drug_url already exists in DB
                            existing_links_df = insert_into_trino.get_existing_drug_link_ids_created_date(
                                schema=trino_schema,
                                table_name="dailymed_dosing_master",
                                drug_urls=unique_links
                            )

                            #maping from drug_url to drug_link_id
                            link_id_map = dict(zip(existing_links_df["drug_url"], existing_links_df["drug_link_id"]))
                            created_date_map = dict(zip(existing_links_df["drug_url"], existing_links_df["created_date"]))
                            print(existing_links_df.columns)
                            #drug_url not found, new UUID
                            # Assigning UUIDs to missing or null drug_link_ids
                            for link in unique_links:
                                if link not in link_id_map or pd.isna(link_id_map[link]):
                                    link_id_map[link] = str(uuid.uuid4())
                                if link not in created_date_map or pd.isna(created_date_map[link]):
                                    created_date_map[link] = pd.to_datetime(now)  # Assigning 'now' for new or missing created_date

                            Final_combined_details['drug_link_id'] = Final_combined_details['drug_url'].map(link_id_map)
                            Final_combined_details["created_date"] = Final_combined_details["drug_url"].map(created_date_map)
                        except Exception as e:
                            print(f"get_existing_drug_link_ids_created_date failed: {str(e)}")
                            logger.error(f"get_existing_drug_link_ids_created_date failed: {str(e)}")
                        # Reordering columns to make drug_link_id the first
                        cols = ['drug_link_id'] + [col for col in Final_combined_details.columns if col != 'drug_link_id']
                        Final_combined_details = Final_combined_details[cols]

                    for df in [All_Final_Drug_details2, Final_combined_details]:
                        # df['created_date'] = pd.to_datetime(now)
                        df['modified_date'] = pd.to_datetime(now)
                        df['processed_by'] = user_id
                        df['job_id'] = job_id
                        df['active_flag'] = "Y"
                    # insert_dataframe_to_trino(Trino_Final_combined_details, "bts_dosing_and_ct_gen_ai_tbl")
                    columns=await load_column_names(CONFIG_JSON_PATH,"trino_dailymed_dosing_raw_tbl_column_names")
                    #ndcs_to_update = Trino_Final_combined_details["drug_url"].dropna().unique().tolist()
                    # df.columns = [c.lower() for c in df.columns]
                    if list_links:
                        Trino_Final_combined_details = Final_combined_details[columns].copy()
                        Trino_Final_combined_details = Trino_Final_combined_details[columns]
                        # ndcs_to_update = Trino_Final_combined_details["drug_url"].dropna().unique().tolist()
                        Trino_Final_combined_details.replace({'': None, 'N/A': None}, inplace=True)

                        logger.info(f"[DEBUG] Initializing dbQueryClient")
                        try:
                            # conn=TrinoConnection()
                            # logger.info(f"[DEBUG] Connection instance type: {type(conn)}")
                            # insert_into_trino=dbQueryClient(conn)
                            Trino_Final_combined_details.columns = [c.lower() for c in Trino_Final_combined_details.columns]
                            logger.info(f"[DEBUG] dbQueryClient connection is established")
                            # insert_into_trino.deactivate_old_rows(schema=trino_schema, table=trino_table, ndc_list=ndcs_to_update, modified_time=now)
                            insert_into_trino.insert_dataframe(df=Trino_Final_combined_details,schema=trino_schema,table_name=trino_table)
                            # Final_combined_details.rename(columns={'ndc': 'drug_url'}, inplace=True)
                            if list_links:
                                rows2=[]
                                url_link=list_links[i]["Drug_URL"]
                                related_ndcs_list=list_links[i]["NDCs"]
                                for ndc in related_ndcs_list:
                                    row = {
                                        "drug_url": url_link,
                                        "ndcs": ndc,

                                    }
                                    rows2.append(row)

                                # Then convert to DataFrame
                                # import pandas as pd
                                df_link_ndc_mapping = pd.DataFrame(rows2)
                                df_link_ndc_mapping["drug_link_id"] = df_link_ndc_mapping["drug_url"].map(link_id_map)
                                df_link_ndc_mapping['created_date'] = Final_combined_details["drug_url"].map(created_date_map)
                                df_link_ndc_mapping['modified_date'] = pd.to_datetime(now)
                                df_link_ndc_mapping['processed_by'] = user_id
                                df_link_ndc_mapping['job_id']= job_id
                                df_link_ndc_mapping['active_flag'] = "Y"
                                mapping_table="dosing_ndc_master"
                                insert_into_trino.insert_dataframe(df=df_link_ndc_mapping,schema=trino_schema,table_name=mapping_table)

                                all_mapping_df = pd.concat([all_mapping_df, df_link_ndc_mapping], ignore_index=True)

                        except Exception as e:
                            logger.error(f"insert_dataframe failed: {str(e)}")
                    if All_Final_combined_details.empty:
                        All_Final_combined_details = Final_combined_details
                    else:
                        All_Final_combined_details = pd.concat([All_Final_combined_details, Final_combined_details], ignore_index=True)

                # Mark NDC as processed
                processed_id = current_ndc if input_file is not None else current_drug_url
                # controller.processed_ndcs.add(processed_id)
                #controller.processed_ndcs.add(current_ndc)
                controller.processed_ndcs.add(processed_id)
                # controller.processed_dicts.append(processed_id)
                processed_link_dict.append(list_links[i])
                if update_section_status:
                    await update_section_status(SectionProcessingStatus.COMPLETED_SECTION.value, current_drug_url)


                # SAVE CHECKPOINT EVERY 10 PROCESSED ITEMS
                #if (i + 1) % 10 == 0:
                await save_checkpoint(
					job_id,
                    controller.processed_ndcs,
                    i + 1,
                    All_Final_Drug_details2,
                    All_Final_combined_details,
                    processed_link_dict
                )
                await ndcs_pause_info(
                    job_id,
                    controller.processed_ndcs,
                    i + 1,
                    All_Final_Drug_details2,
                    All_Final_combined_details
                )

            except Exception as e:
                logger.error(f"Error processing NDC {processed_id}: {e}")

                if update_section_status:
                    await update_section_status(SectionProcessingStatus.FAILED_SECTION.value, processed_id)

                error_log_file.append({
                    "NDC": processed_id,
                    "error": str(e),
                    "timestamp": datetime.now().isoformat()
                })
                continue

            #finally:
                #if status_tracker:
                    #status_tracker.clear_status(current_ndc)

        # FINAL OUTPUT GENERATION
        # timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        # now_str = now.strftime("%Y%m%d_%H%M%S")
        now_str = now.replace('-', '').replace(':', '').replace(' ', '_')
        output_filename = f"outputs/Async_Final_Dosage_details_{job_id}_{now_str}.xlsx"
        if not All_Final_combined_details.empty:
            All_Final_combined_details.to_excel(output_filename, index=False)
            logger.info(f"[SUCCESS] Output saved: {output_filename}")
        output_filename_all_mapping_df = f"outputs/link_ndc_mapping_{job_id}_{now_str}.xlsx"
        if not all_mapping_df.empty:
            all_mapping_df.to_excel(output_filename_all_mapping_df, index=False)
            logger.info(f"[SUCCESS] All_mapping Output saved: {output_filename_all_mapping_df}")

        # Save error log if any
        if error_log_file:
            error_log_path = f"log/error_log_{job_id}_{now_str}.xlsx"
            os.makedirs("log", exist_ok=True)
            pd.DataFrame(error_log_file).to_excel(error_log_path, index=False)
            logger.info(f"[ERROR LOG] Error log saved: {error_log_path}")

        # Clean up checkpoint files
        #checkpoint_files = [
        #    f"checkpoints/checkpoint_{job_id}.json",
        #    f"checkpoints/drug_details_{job_id}.xlsx",
        #    f"checkpoints/combined_details_{job_id}.xlsx"
        #]
        #for file in checkpoint_files:
        #    if os.path.exists(file):
        #        os.remove(file)

        end_time = time.perf_counter()
        print(f"Total execution time: {end_time - start_time:.2f} seconds")

		# all_ndcs = [item["NDC"] for item in NDCParsed]  # or however you're storing NDCs
        # link_or_NDCParded=NDCParsed if NDCParsed else list_links
        if NDCParsed:
            if isinstance(NDCParsed, pd.DataFrame):
                all_ndcs = list(NDCParsed["NDC"].dropna().unique())
            else:
                all_ndcs = list(set(NDCParsed))  # fallback if it's already a list
        else:
            # all_ndcs= list_links
            all_ndcs = [item["Drug_URL"] for item in list_links]
        processed_ndcs_list = list(controller.processed_ndcs)
        pending_ndcs_list = list(set(all_ndcs) - controller.processed_ndcs)
        return {
            "status": "completed",
            "output_file": output_filename,
            "output_file_ndc_mapping": output_filename_all_mapping_df,
            "total_processed": len(controller.processed_ndcs),
			"total_ndcs": controller.total_items,
            "pending": controller.total_items - len(controller.processed_ndcs),
            "processed_ndcs_list": processed_ndcs_list,
            "pending_ndcs_list": pending_ndcs_list,
            "errors": len(error_log_file),
            "execution_time": f"{end_time - start_time:.2f} seconds"
        }

    except asyncio.CancelledError:
        # Save checkpoint when cancelled
		# link_or_NDCParded=NDCParsed if NDCParsed else list_links
        if NDCParsed:
            all_ndcs = [item["NDC"] for item in NDCParsed]
        else:
            all_ndcs = list_links
        processed_ndcs_lis = list(controller.processed_ndcs)
        pending_ndcs_lis = list(set(all_ndcs) - controller.processed_ndcs)

        await save_checkpoint(
            job_id,
            controller.processed_ndcs,
            controller.current_progress,
            All_Final_Drug_details2 if 'All_Final_Drug_details2' in locals() else pd.DataFrame(),
            All_Final_combined_details if 'All_Final_combined_details' in locals() else pd.DataFrame(),
            processed_link_dict
        )
        await ndcs_pause_info(
                    job_id,
                    controller.processed_ndcs,
                    i + 1,
                    All_Final_Drug_details2,
                    All_Final_combined_details
                )
        logger.info(f"[CANCELLED] Operation cancelled and checkpoint saved for job: {job_id}")
        return {
            "status": "cancelled",
            "processed_count": len(controller.processed_ndcs),
			"total_ndcs": len(all_ndcs),
            "processed_ndcs": processed_ndcs_lis,
            "pending_ndcs": pending_ndcs_lis,
            "checkpoint_saved": True
        }

    except Exception as e:
        logger.error(f"[ERROR] Unexpected error in main: {e}")
        return {
            "status": "failed",
            "error": str(e),
            "processed_count": len(controller.processed_ndcs) if controller else 0
        }

if __name__ == "__main__":
    asyncio.run(main("test_job", "input/test_input_1ndc.xlsx"))