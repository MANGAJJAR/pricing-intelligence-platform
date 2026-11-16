from bs4 import BeautifulSoup
from langchain_text_splitters import HTMLSectionSplitter
import requests
import httpx
import re
import pandas as pd
import os, time
import json
import dotenv
from urllib.parse import urljoin
from json_repair import repair_json
import html

import re
missed_ndcs =[]

err = {'NDC': [],'error': []}
err_ndcs = pd.DataFrame(data=err)
err_cnt = 0

#import nest_asyncio
# Load environment variables

dotenv.load_dotenv("config/.env")

client_id = os.getenv("CLIENT_ID")
client_secret = os.getenv("CLIENT_SECRET")
token_url = os.getenv("token_url")
iliad_url = os.getenv("endpoint")

ndc_url = "https://dailymed.nlm.nih.gov/dailymed/search.cfm?labeltype=human&query="

def get_chat_access_token():

    response = requests.post(
        url=token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret
        }
    )
    response.raise_for_status()
    data = response.json()
    token = data["access_token"]
    return f"Bearer {token}"

def scrape_article_details(ndc,article_url):

    dosage_info=''
    #print(f" fetching URL: {article_url}")
    try:
        response = requests.get(article_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        drug_label = soup.find("span", id="drug-label").text
        #print(drug_label)
        drug_info_section = soup.find('ul', class_='drug-information')
        packager_text = "Manufacturer is not present."

        if drug_info_section:
            for li in drug_info_section.find_all('li'):
                if 'Packager:' in li.text:
                    packager_text = li.text.replace('Packager:', '').strip()
                    break

        # print("Manufacturer-------------------")
        # print(packager_text)
        drug_label_sections = soup.find("div", class_="drug-label-sections")  # Verify selector!

        drug_info_sections = drug_label_sections.find_all("li")

#         #######print(drug_label_sections)

    except Exception as e:
        #print(f"An error occurred: {e}")
        #err_ndcs.loc[err_cnt] = [ndc,"Unable to access Link"]
        #err_cnt = err_cnt+1
        return None

    return drug_label_sections,drug_label,packager_text

def extract_details2(prompt, sample_text="", image_url=None):
    AUTH_TOKEN = get_chat_access_token()
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
            "text": prompt+sample_text
        })
    else:
        messages[0]["content"].append({
            "type": "text",
            "text": prompt+sample_text
        })

    iliad_url = "https://qa.api.abbvienet.com/iliad/abbvie"

    response = requests.post(
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

    return markup

def extracting_indication_details(Sample_text):

    AUTH_TOKEN = get_chat_access_token()
    print("Entered into extracting_indication_details")

    prompt= """You are a medical pharma data text bot that extracts drug Indications from the given text .
Pick the disease, indication tag and usage from the Context
and provide the output in the form of json. Strictly extract the disease, indication and usage only.
In case sub indications are present
example: platinum-resistant recurrent epithelial ovarian, fallopian tube or primary peritoneal cancer
or platinum-sensitive recurrent epithelial ovarian, fallopian tube, or primary peritoneal cancer ,extract those also as indications, if subindications not available mark sub indications as none.
Extract the information of line of treatment like first line, second line, maintenance etc, from usage and create a tag as LineofTherapy.
Check whether it is a combination or single therapy and create a tag as Typeoftherapy. Retreive the drug required for combination
therapy and tag this as combination drug.  ***STRICTLY Extract all the indications. Dont ask questions.
Do not provide any limitation notice. EXTRACT all indications. Do not leave any indication ***

These are the definitions of Parameters which has to be considered from context and should be STRICTLY followed:
'Disease_full': full name of the disease(i.e., Full disease area description). Example: 'Urothelial Carcinoma', 'MSI-H/dMMR Solid Tumors (Tissue-Agnostic)' etc,.
'Disease_short': An abbreviated or shortened version of Disease_full name. Example: 'Urothelial Carcinoma → Urothelial', 'MSI-H/dMMR Solid Tumors (Tissue-Agnostic) → MSI-H/dMMR' etc.
'Indication': The specific disease or condition that the drug is intended to treat.
'Subindications':  Any sub-types, variants, or additional contexts under the main indication.
'Usage': The medical context or scenario in which the drug is used.
'LineofTherapy': The specific treatment line or sequence in therapy. Examples: "first-line", "relapsed", "maintenance", "Second-line or later (post-chemotherapy)", 'newly diagnosed', 'Consolidation', 'Advanced (First-line or beyond)' etc,.
'Line_of_Therapy_Simplified': A standardized short form of LineofTherapy, It should be one of '1L', '1L+', '2L', '2L+', '3L+', '1L or 2L', '1L or 2L+', 'Adjuvant', 'Neoadjuvant'. Examples: 'first-line → 1L', 'relapsed/refractory → 2L+', 'Neoadjuvant, Adjuvant → 'Neoadjuvant, Adjuvant' etc,.
'Combinationdrug': drug which is used in combination with this drug.
'Combo_Therapy_Flag': Indicates whether the drug is given in combination with others. Flag Value is 'Yes' if any combination drug is present, otherwise 'No'.
'Combo_1': First combination partner, Extract first named combination drug if availabe else none
'Combo_2': Second combination partner, Extract second named combination drug if availabl else none
'Combo_3': Third combination partner, Extract third named combination drug if available else none



The output should be in following json format:
```
{"indications": [{"Disease_full": "", "Disease_short": "", "Indication": "", "Subindications": "", "Usage": "", "LineofTherapy": "", "Line_of_Therapy_Simplified": "", "TypeofTherapy": "", "Combinationdrug": "", "Combo_Therapy_Flag": "", "Combo_1": "", "Combo_2": "", "Combo_3": ""}]}
```

Example of output:
{
    "indications": [
        {
            "Disease_full": "Non-Small Cell Lung Cancer",
            "Disease_short": "NSCLC",
            "Indication": "HR+, HER2- Metastatic Breast Cancer (First-line, Letrozole combo)",
            "Subindications": "subindication1",
            "Usage": "usage1",
            "LineofTherapy": "Persistent/Recurrent/Metastatic",
            "Line_of_Therapy_Simplified": "1L or 2L",
            "TypeofTherapy": "typeoftherapy1",
            "Combinationdrug": "combinationdrug1",
            "Combo_Therapy_Flag": "Yes",
            "Combo_1": "Axitinib",
            "Combo_2": "none",
            "Combo_3": "none"
        }
    ]
}

     **NOTES**
    1. Extract details of the Indication, subindication and corresponding filelds mentioned in the json format from *context* .
    2. Add a new object in the json list If a line of Indication has Two or more indications. Separate those in different objects of json list.
    3. Don't provide any other information apart from JSON output.
    4. Output should be in valid json format. Don't provide any additional text in output. Provide only JSON.
    5. Use the information provided in below context only. If the context doesn't provide the relavant information then mention as N/A in the particular key of JSON output.
    The the below text as context.

    Context:

"""
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

# Send the POST request to the API
    response = requests.post(
    url=iliad_url+"/api/v1/chat/claude-3.5-sonnet",
    json={"messages": messages, "temperature": 0},
    headers={
        "Authorization": AUTH_TOKEN,
        'Accept': 'application/img',
        'Content-Type': 'application/json'
    },
    timeout=120  # Set a timeout for the request  # this is taking unusually longer. change to 20
)
    response.raise_for_status()  # Raise an exception for HTTP errorslogger.info("Having the http response from img_json_data_1 ")
#logger.info("Having the http response from img_json_data_1 ")
#logger.info(f"Index: {index}, HTTP Status Code: {response.status_code}") #, Response Text: {response.text}")  # Log the HTTP response

    response.json()['completion']['content']

    markup = response.json()['completion']['content']
    print(f"indication details prompt output."+ "-"*10)
    print(markup)

    return markup

def extracting_clinicalTrial_details(stringc, Clinical_text):
    AUTH_TOKEN = get_chat_access_token()
    prompt = """You are a medical pharma data text bot that extracts Median duration of response, study ID, for"""+ stringc +""" from the given text.
    These are the definitions of Parameters which has to be considered from context and should be STRICTLY followed:
    'dosage' : dosage of the drug
    'study_results' : list of features for the drug indication and study.
    'indication' : name of disease for which the drug is treated. Don't consider the administration form as a new indication.
    'sub-indication' : value of sub-indications for that drug
    'Patient_selection' : Selection criteria of patients for study. Example: All Patients, CPS≥1, CPS≥10.
    'study ID' : the unique identifier about the clinical trial performed for the drug and indication. example:  KEYNOTE-204
    'Median duration of response' : Duration of response in Median in months or years (range). This should be expressed with appropriate units (e.g., months, years). Examples: 11.1 (0.0+, 11.1) or three-year study etc.
    'Max duration of response' : The longest time a patient continued to respond to the treatment in the study. This should be expressed with appropriate units (e.g., months, years). If the exact value is not directly given in the context, use the upper bound of the confidence interval (e.g., 26.7 months). If the upper bound is also not available, use the median duration of response as the Max duration of response. Always return the value along with its unit (e.g., "26.7 months", "2 years").
    'Max duration of response (weeks)' : Max duration of response, expressed in weeks. This value should be derived by converting the Max duration of response into weeks using the logic. If the duration is given in months, convert it using the formula: weeks = (months × 30) ÷ 7. (e.g., 11.2 months → (11.2 × 30) ÷ 7 = 48 weeks). If the duration is given in years, convert it using the formula: weeks = years × 52. (e.g., 2 years → 2 × 52 = 104 weeks).
    output json format:  {
        "study_results": [
            {
            "indication": ,
            "sub-indication": ,
            "study_id": ,
            "medications": ,
            "Median duration of response": ,
            "Max duration of response": ,
            'Max duration of response (weeks)'
            "dosage":
            }
        ]
    }
     **NOTES**
    1. Add a new section of in the output if for the same drug and indication,
    2. Don't provide any other information apart from JSON output.
    3. Use the information provided in below context only. If the context doesn't provide the relavant information then mention as N/A in the particular key of JSON output.
    The the below text as context.
    4. Use provided Indication and Sub-indication.
    Context:
    """

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

    # Send the POST request to the API
    response = requests.post(
    url=iliad_url+"/api/v1/chat/claude-3.5-sonnet",
    json={"messages": messages, "temperature": 0},
    headers={
        "Authorization": AUTH_TOKEN,
        'Accept': 'application/img',
        'Content-Type': 'application/json'
    },
    timeout=120  # Set a timeout for the request  # this is taking unusually longer. change to 20
    )
    response.raise_for_status()  # Raise an exception for HTTP errorslogger.info("Having the http response from img_json_data_1 ")
    #logger.info("Having the http response from img_json_data_1 ")
    #logger.info(f"Index: {index}, HTTP Status Code: {response.status_code}") #, Response Text: {response.text}")  # Log the HTTP response

    response.json()['completion']['content']

    markup1 = response.json()['completion']['content']
    #print(markup1)
    return markup1

def extracting_adverse_reaction_details(stringc, adverse_reaction_text):
    AUTH_TOKEN = get_chat_access_token()
    print("Entered into extracting_adverse_reaction_details function")
    prompt = """You are a medical pharma data text bot that extracts Median duration of response, study ID, for"""+ stringc +""" from the given context.
    These are the definitions of Parameters which has to be considered from context and should be STRICTLY followed:

    'study_results' : list of features for the drug indication and study.
    'indication' : The specific disease or condition that the drug is intended to treat.
    'sub-indication' : Any sub-types, variants, or additional contexts under the main indication.
    'study_id' : the unique identifier about the clinical trial performed for the drug and indication. example:  KEYNOTE-204
    'Median duration of treatment' : Duration of treatment in Median in months or years (range). This should be expressed with appropriate units (e.g., months, years). Examples: 11.1 (0.0+, 11.1) or three-year study etc.
    'Median duration of treatment (weeks)' : Median duration of treatment, expressed in weeks. This value should be derived by converting the Median duration of response into weeks using the logic. If the duration is given in months, convert it using the formula: weeks = (months × 30) ÷ 7. (e.g., 11.2 months → (11.2 × 30) ÷ 7 = 48 weeks). If the duration is given in years, convert it using the formula: weeks = years × 52. (e.g., 2 years → 2 × 52 = 104 weeks). examples: 46 (0.0+, 56)- weeks
    'Max duration of treatment' : The longest time a patient continued to respond to the treatment in the study. This should be expressed with appropriate units (e.g., months, years). If the exact value is not directly given in the context, use the upper bound of the confidence interval (e.g., 26.7 months). If the upper bound is also not available, use the median duration of response as the Max duration of response. Always return the value along with its unit (e.g., "26.7 months", "2 years").
    'Max duration of treatment (weeks)' : Max duration of treatment, expressed in weeks. This value should be derived by converting the Max duration of response into weeks using the logic. If the duration is given in months, convert it using the formula: weeks = (months × 30) ÷ 7. (e.g., 11.2 months → (11.2 × 30) ÷ 7 = 48 weeks). If the duration is given in years, convert it using the formula: weeks = years × 52. (e.g., 2 years → 2 × 52 = 104 weeks).

    output json format:
    {
        "study_results": [
            {
            "indication": ,
            "sub-indication": ,
            "study_id": ,
            "Median duration of treatment": ,
            "Median duration of treatment (weeks)": ,
            "Max duration of treatment": ,
            "Max duration of treatment (weeks)": ,
            }
        ]
    }

     **NOTES**
    1. Add a new object in the json list if study_id, sub-indication or indication varies.
    2. Don't provide any other information apart from JSON output.
    3. Extract details for the given Indication.
    4. Output should be in valid json format. Don't provide any additional text in output. Provide only JSON.
    5. Use the information provided in below context only. If the context doesn't provide the relavant information then mention as N/A in the particular key of JSON output.
    The the below text as context.



    Context:
    """

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

    # Send the POST request to the API
    response = requests.post(
    url=iliad_url+"/api/v1/chat/claude-3.5-sonnet",
    json={"messages": messages, "temperature": 0},
    headers={
        "Authorization": AUTH_TOKEN,
        'Accept': 'application/img',
        'Content-Type': 'application/json'
    },
    timeout=120  # Set a timeout for the request  # this is taking unusually longer. change to 20
    )
    response.raise_for_status()  # Raise an exception for HTTP errorslogger.info("Having the http response from img_json_data_1 ")
    #logger.info("Having the http response from img_json_data_1 ")
    #logger.info(f"Index: {index}, HTTP Status Code: {response.status_code}") #, Response Text: {response.text}")  # Log the HTTP response

    response.json()['completion']['content']

    markup1 = response.json()['completion']['content']
    print("adverse reactions details prompt output."+ "-"*10)
    print(markup1)
    #print(markup1)
    return markup1

def scrape_clinical_and_indication_details(ndc, drug_label_sections):
    try:
        sections = drug_label_sections.find_all("li")
        # Header_split_list = [{"metadata":None,"content":None}]
        dosage_info =""
        # header_text="all clinical details"

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
            # if re.match(pattern=pattern,string=normalized_section_title_text) is not None:
            #     print(normalized_section_title_text)
            if re.search(pattern, normalized_section_title_text):
                print(normalized_section_title_text)
            # if "INDICATIONS AND USAGE" in section_title_text:
                indication_usage_text = section.find("div", class_="Section toggle-content closed long-content")
                # if not section_div:
                #     continue

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
                    print(indication_usage_text)
                    dosage_info = indication_usage_text.text
                    print(dosage_info)

            # ============================
            # CLINICAL STUDIES
            # ============================
            # if "CLINICAL STUDIES" in section_title_text:
            #     clinical_studies_text = section.find("div", class_="Section toggle-content closed long-content" )
            #     all_clinical_content=clinical_studies_text.text
                    ######print(clinical_studies_text)
                # clinical_indications = clinical_studies_text.find_all("div", class_="Section")
                # if len(clinical_indications) > 0:
                # ######print(type(clinical_indications))
                #     for div_s in clinical_indications:
                #         # print(div_s)
                #         # header = div_s.find("h2")
                #         images = div_s.find_all('img')
                #         img_text=[]
                #         for img in images:
                #             print(img)
                #             relative_url = img.get('src')
                #             base_url = "https://dailymed.nlm.nih.gov"

                #             image_url = urljoin(base_url, relative_url)

                #             print(image_url)

                #             if image_url:
                #                 prompt="""
                #                 Analyze the given image and extract relevant information:

                #                 - If the image is a **graph or chart**, identify it as such and describe it.
                #                 - If the image is a **table**, convert it into a Markdown table format.
                #                 """
                #                 # extract all inofrmation from the image"
                #                 image_info = extract_details2(image_url,prompt)
                #                 img_content = f"\n\nImage Information:\n{image_info}\n\n"

                #                 img_text.append(img_content)
                #                 # img.decompose()
                #                 img.replace_with(BeautifulSoup(img_content, 'html.parser'))


                #     content=clinical_studies_text.text
                #     # header_splits = {"metadata":header_text,"content":content}
                #     # Header_split_list.append(header_splits)
                # else:
                    # sentence = "<html> <body>" + str(clinical_studies_text) + "</body> </html>"
                    # soup = BeautifulSoup(sentence, 'html.parser')
                    # div_text = soup.get_text()
                    # content=clinical_studies_text.text
                    # header_splits = {"metadata":header_text,"content":content}
                    # Header_split_list.append(header_splits)
                    # print(Header_split_list)
            # ============================
            # ADVERSE REACTIONS
            # ============================
            if "ADVERSE REACTIONS" in section_title_text:
                adverse_reactions_text = section.find("div", class_="Section toggle-content closed long-content" )
                all_adverse_reactions_content=adverse_reactions_text.text
                break

            else:
                None

    except Exception as e:
        print(f"Error during scraping: {e}")
        None

    return dosage_info, all_adverse_reactions_content

def Populating_Required_details(NDCParsed,ndc_url):
    print("Populating function called")
    d = {'NDC': [],'Drug_name': [], 'Parsed': []}
    e= {'NDC': [],'Drug_name': [],'Manufacturer':[], 'Parsed': []}
    Drug_details = pd.DataFrame(data=e)
    Drug_details_header = pd.DataFrame(data=e)
    Clinical_trial_details = pd.DataFrame(data=d)
    dosage_administration_details = pd.DataFrame(data=d)
    all_adverse_reactions_details_df = pd.DataFrame(data=d)
    errors=pd.DataFrame(e)
    j=0
    inddet = 0
    for i in range(len(NDCParsed)):
    #for i in range(20):
        # print(len(NDCParsed))
        print(len(NDCParsed)-i)
        try:
            NDCi = str(NDCParsed.iloc[i].values[0])

            NDCi = NDCi.replace('-','')
            NDCi = NDCi.zfill(11)
            print(NDCi)

            #NDCi ='00006302601' #'6521961299'
#'00002448354' #'00054028259' #"00006302601" #"25021022320"
            #print(NDCi)
            article_url = ndc_url + NDCi
            print(article_url)
            article_details,label,packager = scrape_article_details(NDCi,article_url)

            # indication_details = scrape_indication_use_details(NDCi,article_details)
            indication_details, all_adverse_reactions_details = scrape_clinical_and_indication_details(NDCi,article_details)
            print("scrapping is done")
            # print(len(indication_details))
            # print(type(indication_details))
            # ============================
            # INDICATIONS AND USAGE
            # ============================
            if isinstance(indication_details,str):
                print("indication string is found")
                print(f"Indication string --- {indication_details}")
                extracted_indication_details = extracting_indication_details(indication_details)
                # print(extracted_indication_details)
                soup = BeautifulSoup(extracted_indication_details, 'html.parser')
                t1 = soup.prettify()
                Drug_details.loc[i] = [NDCi,label,packager,t1]
            elif isinstance(indication_details,list):
                print("indication list is found")
                for indication in range(len(indication_details)):
                    extracted_indication_details = extracting_indication_details(indication_details[indication].page_content)
                    # print(extracted_indication_details)
                    soup = BeautifulSoup(extracted_indication_details, 'html.parser')
                    t1 = soup.prettify()
                    Drug_details_header.loc[inddet] = [NDCi,label,packager,t1]
                    inddet = inddet + 1

            # ============================
            # CLINICAL STUDIES
            # ============================
            # print("entered into clinical details extraction")

            # Clinical_trial_details.loc[i] = [NDCi,label,Clinical_details]
            # for det in range(len(Clinical_details)-1):
            #     print("clinical deatils processing")
            #     if len(Clinical_details[det+1]["content"].strip()) > 1:
            #         # print(len(Clinical_details[det+1]["content"].strip()))
            #         try:
            #             extracted_clinical_details = extracting_clinicalTrial_details(Clinical_details[det+1]["content"])
            #             # ndc_status["extracting_clinicalTrial_details"] = "PASS"
            #         except Exception as e:
            #             # ndc_status["extracting_clinicalTrial_details"] = "FAILED"
            #             print(f"[{NDCi}] extracting_clinicalTrial_details: {e}")

            #         soup_clinical_trial = BeautifulSoup(extracted_clinical_details, 'html.parser')
            #         t1_clinical_trial = soup_clinical_trial.prettify()
            #         print(t1_clinical_trial)
            #         Clinical_trial_details.loc[j] = [NDCi,label ,t1_clinical_trial]
            #         j = j+1


            # ============================
            # dosage administration
            # ============================
            dosage_details = scrape_dosage_and_administration_details(NDCi, article_details)
            dosage_administration_details.loc[i] = [NDCi,label,dosage_details]

            # ============================
            # ADVERSE REACTIONS
            # ============================
            all_adverse_reactions_details_df.loc[i] = [NDCi,label,all_adverse_reactions_details]

        except Exception as e:
            print(f"An error occurred: {e}")
            Drug_details.loc[i] = [NDCi,"Drug not found", "Not Found on Dailymed"]
            # errors=e= {'NDC': [],'Drug_name': [],'Manufacturer':[], 'Parsed': []}
            errors = pd.concat([errors, pd.DataFrame([{
                        "Drug_name": NDCi,
                        "Manufacturer": "error",
                        "Parsed": e,
                        # "error": str(e),
                        # "error": error
                    }])], ignore_index=True)


        ##print(Drug_details_header)
        ##print(len(Drug_details_header))
        if len(Drug_details_header)>0:
            Drug_details_final = pd.concat([Drug_details,Drug_details_header],axis = 0)
        else:
            Drug_details_final = Drug_details
    #errors.to_excel("outputs/errors_file_Indication_NDCs_20_may.xlsx")
    Drug_details_final = Drug_details_final.reset_index()

    print(f"drug details len - {len(Drug_details_final)}")
    return Drug_details_final,dosage_administration_details,all_adverse_reactions_details_df
    #return dosage_administration_details


# def extraction_in_tabular_format(Drug_details):
#     # d = {'NDC': [],'Drug_name': [], 'Parsed': []}
#     Final_Drug_details=pd.DataFrame()
#     for i in range(len(Drug_details)):
    #     NDCi=Drug_details.loc[i,'NDC']
    #     try:
    #         #print(i)
    #         article_indication = Drug_details.loc[i,'Parsed']
    #         Drug_name = Drug_details.loc[i,'Drug_name']
    #         if Drug_name == "Drug not found":
    #             Final_Drug_details.loc[i] = pd.Series(dtype='float64')
    #         else:
    #             json_match = re.search(r'(\[.*\])', article_indication, re.DOTALL)
    #             if json_match:

    #                 json_data = json_match.group(1) #group(1) the () content is considered irrespective of other text in re search

    #                 data = json.loads(json_data)
    #             #data = json.loads(article_indication)

    #     # Convert JSON data to a pandas DataFrame
    #             df = pd.json_normalize(data)
    #             df['NDC'] = Drug_details.loc[i,'NDC']
    #             df['Drug_name'] = Drug_details.loc[i,'Drug_name']

    #             if i == 0:
    #                 Final_Drug_details = df
    #             else:
    #                 Final_Drug_details = pd.concat([Final_Drug_details, df], ignore_index=True)

    #     except Exception as e:
    #             print(f"An error occurred: {e}")
    #             # Final_Drug_details.loc[i] = [NDCi,"Drug not found" ,"Not Found on Dailymed"]

    # #Final_Drug_details = Final_Drug_details.iloc[:, [6,7,0,1,2,3,4,5]]
    # Final_Drug_details.to_csv("outputs/Final_Drug_details.csv")
    # return Final_Drug_details

def extraction_in_tabular_format(Drug_details):
    Final_Drug_details2=pd.DataFrame()
    for i in range(len(Drug_details)):
        try:
            # print("entered into try block")
            # print(i)
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
                #data = json.loads(article_indication)

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
            # return print(f"An error occurred: {e}")
                #####print(f"An error occurred: {e}")
            # Final_Drug_details2.loc[i] = [NDCi,"Drug not found" ,"Not Found on Dailymed"]

    # Final_Drug_details = Final_Drug_details.iloc[:, [6,7,0,1,2,3,4,5]]
    # print(Final_Drug_details2)
    Final_Drug_details2.to_csv("outputs/Final_Drug_details3_21_May_1.csv")
    print(f"Final_Drug_details3:{len(Final_Drug_details2)}")
    return Final_Drug_details2

def extract_clinical_trial_in_tabular_format(Clinical_trial_details):
    for i in range(len(Clinical_trial_details)):
        #print (i)
        ######print(Clinical_trial_details.loc[i,'Parsed'])
        article_indication = Clinical_trial_details.loc[i,'Parsed']
        Drug_name = Clinical_trial_details.loc[i,'Drug_name']
        ######print(i,Drug_name)
        j = 0
        if Drug_name == "Drug not found":
            None
            #Error_clinical_details = pd.Series(dtype='float64')
            #To BE added later
        else:
            json_match = re.search(r'(\[.*\])', article_indication, re.DOTALL)
            if json_match:

                json_data = json_match.group(1) #group(1) the () content is considered irrespective of other text in re search

                data = json.loads(json_data)
            #data1 = json_repair.loads(article_indication)
            #data1 = repair_json(article_indication)
            #data = json.loads(data1)
                ##print(data)
    # Convert JSON data to a pandas DataFrame
                df = pd.json_normalize(data)
            #df = pd.json_normalize(data)
                df['NDC'] = Clinical_trial_details.loc[i,'NDC']
                df['Drug_name'] = Clinical_trial_details.loc[i,'Drug_name']

                if i == 0:
                    Final_clinical_details = df
                else:
                    Final_clinical_details = pd.concat([Final_clinical_details, df], ignore_index=True)
            else:
                #To Be added
                None

    #Final_Drug_details = Final_Drug_details.iloc[:, [6,7,0,1,2,3,4,5]]
    Final_clinical_details.to_excel("outputs/Clinical_details.xlsx")


def scrape_dosage_and_administration_details(ndc,drug_label_sections):
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
                    # if len(dosage_administration_text.text) > 1000:
                    #     header = dosage_administration_text.find("h2")
                    #     if header:
                    #         sentence = "<html> <body>" + str(dosage_administration_text) + "</body> </html>"
                    #         headers_to_split_on = [("h2", "Header 2")]
                    #         html_splitter = HTMLSectionSplitter(headers_to_split_on)
                    #         html_header_splits = html_splitter.split_text(sentence)
                    #         dosage_administration_info = html_header_splits
                    #         # #print("*"*40)
                    #         # #print(len(dosage_administration_info))
                    #         # #print(dosage_administration_info)
                    #         # #print("*"*40)
                    #     else:
                    #         dosage_administration_info = dosage_administration_text.text
                    # else:
                    dosage_administration_info = dosage_administration_text.text
    #print(dosage_administration_info)
    return dosage_administration_info

def extracting_dosage_details(Stringp, dosage_text):
    AUTH_TOKEN = get_chat_access_token()
    print("Entered into extracting_dosage_details function")
    prompt = """You are a medical pharma data text bot that extracts drug indications and the recommended dosage for """ + Stringp + """along with the frequency
    on which drug should be taken and the total duration of treatment. In case dosage varies with time or cycle extract that too.

    Definitions of Parameters:
    - **Dosage Scenario**: It is array of objects of recommended dosage and other parameters.
    - **Recommended Dosage**: Extract the complete dose and administration schedule for the primary drug and all combination therapies in a single string. Include dosage amount, unit, frequency, modifiers, route (if specified in dosage text), and schedule for all components.
        - If the dosage differs based on weight ranges (e.g., `<45kg`, `≥45kg`), extract **a separate JSON object** for each weight-defined group. Each object should include all relevant fields with values specific to that weight range.
        - If weight based dosage is found then split it based on weight in different objects of Dosage_Scenario.
        - Extract only the core dosage expression (amount, unit, frequency, and immediate schedule), strictly excluding any text detailing indications, conditions for adjustment, or co-administered drugs. For complex schedules, capture the full regimen.
        - For mentions of frequency, replace 'every X weeks' with 'qXW' (e.g., 'every 3 weeks' should become 'q3W', 'every 6 weeks' should become 'q6W'). Apply this transformation consistently.
        - Prefer "once daily" over "daily" for the same frequency.
        - Treat "with" and "+" as equivalent indicators of combination therapy and consistently use "with" in the output.
        - Treat "OR" and ";" as equivalent separators for alternative dosages or combination regimens. Represent these consistently (e.g., using "or").
        - Clearly indicate alternative combination options (e.g., "with drug A or drug B").
        - Be concise, avoiding separate rows for minor phrasing variations while capturing distinct dosage regimens (monotherapy vs. combination).
    - **Cycle Duration (Weeks)**: Determine the duration of a single treatment cycle in weeks. If the text specifies a cycle length in days (e.g., "every 21 days", "28-day cycles"), convert it to weeks (3 weeks, 4 weeks). If multiple cycle durations are mentioned (e.g., based on different phases of treatment or different dosage schedules), provide them separated by "or" (e.g., "3 or 6"). If no clear cycle is defined, use "N/A" or "Continuous" if the treatment is ongoing without defined breaks.
    - **Max Cycles**: Extract the maximum number of planned treatment cycles if explicitly stated, including any associated dosing frequency (e.g., "32 cycles (q3w)"). If multiple possibilities are given, extract the full phrasing for each. If both 'Max Duration (Weeks)' and 'Cycle Duration (Weeks)' have defined numerical values for a given treatment regimen, calculate 'Max Cycles' by dividing 'Max Duration (Weeks)' by 'Cycle Duration (Weeks)' and provide the rounded whole number, including the associated cycle duration frequency if available in the context (e.g., if cycle duration is consistently "every 3 weeks", the output should be "[calculated number] cycles (q3w)"). If there are 'or' conditions for either duration, perform the calculation for each combination and present the results separated by "or", including the associated frequency if available. If the treatment continues until progression or indefinitely, use "N/A (until progression)". If the number of cycles depends on other factors or is described non-numerically, extract that description. Use "N/A" if no information is available. Use "~" for approximate values.
        - e.g., Cycle Duration (Weeks) = 3 or 6 , Max Duration (Weeks) = 104 then Max Cycles = 34.6 cycles (q3w) or 17.3 cycles (q6w) , Calculation: For a 3-week cycle, the maximum number of cycles would be 104/3 = ≈34.67, which rounds to 34.6 cycles. For a 6-week cycle, the maximum number of cycles would be 104/6 = ≈17.33, which rounds to 17.3 cycles.
        - If a numerical value is found, format it to one decimal place (e.g., '17.4').
    - **Max Duration (Weeks)**: Determine the maximum intended duration of the treatment in weeks. Convert any time units (days, months, years) to weeks. If a maximum duration is given (e.g., "up to 52 weeks", "for 2 years"), extract **only the numerical value representing the number of weeks (e.g., 52, 104). If the duration is described using both months and weeks (e.g., "3 months or 12 weeks"), prioritize and extract only the numerical value of weeks (12). If multiple durations are given solely in weeks (e.g., "up to 52 or 78 weeks"), provide the numerical values separated by "or" (e.g., "52 or 78").** If the duration is described as "N/A (until progression)" or is open-ended maintenance, use "Undefined" or a similar clear term. If a median treatment duration is provided, use that value and indicate it as "median ~XX" followed by the number (e.g., "median ~50").
    - **Set Duration Under 1 Year**: Indicate if the 'Max Duration (Weeks)' is less than or equal to 51 weeks. If yes, use "Yes"; if the 'Max Duration (Weeks)' is greater than or equal to 52 weeks, use "No".
    - Total Dosage Estimate - Provide an estimate of the total dosage based on the 'Recommended Dosage' and 'Max Cycles'.
        - If 'Max Cycles' has a numerical value and 'Recommended Dosage' indicates a dose per cycle or a frequency within a defined cycle, represent the estimate as "[dosage amount] × [number of cycles] cycles".
        - If either 'Recommended Dosage' or 'Max Cycles' have "OR" options (and 'Max Cycles' contains numerical values), perform the multiplication for each combination and present the results separated by "OR".
        - If 'Max Cycles' is "N/A" (until progression), "Continuous", or similar terms indicating an undefined endpoint, use "Continuous dosing" or a similar phrase. If the 'Recommended Dosage' specifies a dose and frequency, you can include that context (e.g., "Continuous dosing of 100 mg daily").
        - If 'Recommended Dosage' is highly variable (e.g., weight-based without a defined duration or cycles), use "Variable" or "Based on body weight".
        - If 'Recommended Dosage' includes combination therapy, integrate the total estimate for the primary drug (if calculable) with a description of the combination therapy's duration or nature (e.g., "200 mg × 32 cycles + continuous [combination drug]").
    - **Yearly Dosage**: Estimate the total dosage of the primary drug administered within one year (52 weeks), derived from the "Total Dosage Estimate".
        - If "Total Dosage Estimate" is in the format "[amount] mg × [number] cycles", calculate the total dosage (amount × number). If there are "or" options, calculate each total dosage. Requiere only values of "Yearly Dose".
        - If multiple "or" options result in different yearly totals, list them separated by "or". If multiple "or" options result in the same yearly total, list that total only once. (e.g., if 3400 mg or 3400 mg then value is 3400 mg)
        - If "Total Dosage Estimate" indicates "Continuous dosing" or a similar phrase, and if a daily or weekly dose is specified in the "Recommended Dosage", calculate the estimated yearly dose based on that frequency. If the continuous dosing doesn't have a clear per-day/week dose, use "Continuous dosing (yearly estimate may vary)".
        - If "Total Dosage Estimate" is "Variable" or "Based on body weight", then "Yearly Dosage" should also be "Variable" or "Based on body weight".
        - For estimates involving a limited treatment duration, if a total dose over that period can be calculated, provide that total and indicate the duration. If it's continuous over a limited duration, estimate the total for that duration.
        - If "Total Dosage Estimate" includes combination therapies, focus the yearly calculation on the primary drug if quantifiable, and append information about continuous combination drugs.
        - The output should be the dosage value and unit (e.g., '3400 mg'). Do not include '/year' or similar time indicators.
    - **Doses per Year**: Calculate the approximate number of doses a patient would receive in a 365-day year based on the frequency specified in the "Recommended Dosage" (e.g., "q3w" -> approximately 17). If multiple dosing frequencies are mentioned (e.g., "weekly then bi-weekly"), calculate an approximate average annual number of doses considering the duration of each phase if that information is available in other columns like "Max Cycles" or "Max Duration (Weeks)". Round the final numerical result to the nearest whole number. If the "Recommended Dosage" offers multiple frequencies (e.g., "q3w OR q6w"), calculate and provide both rounded yearly doses separated by "or" (e.g., "17 or 9"). Remove any parentheses or non-numerical text associated with the frequency. If no clear frequency can be determined, output "N/A".
    - **Flat Dose**: Extract the numerical value of the flat dose in milligrams (mg) if explicitly stated in the "Recommended Dosage" (e.g., "200 mg IV q3w" -> "200 mg"). If the "Recommended Dosage" indicates a weight-based dose (e.g., "2 mg/kg every 3 weeks (max 200 mg)") and a specific weight is available in the "Weight (kg)" column, calculate the flat dose based on that weight, respecting any maximum dose. The output should be the dosage value and unit (e.g., '200 mg'). If a weight-based dose is mentioned but no specific weight is available, output "NA".
    - **Dosing Segment Used**: Identify and provide the specific portion of the "Recommended Dosage" that was used to determine the "Flat Dose", "mg per Dose", and "Doses per Year". For example, if the "Recommended Dosage" is "200 mg IV q3w OR 400 mg IV q6w" and the calculations were based on the first option, output "200 mg IV q3w". If a weight-based dose was capped by a maximum, include that information (e.g., "2 mg/kg q3w (max 200 mg)"). For complex or multi-phase dosing, provide the segment that was primarily used for the yearly calculation or indicate "Complex Dosing - See Recommended Dosage" if a single segment isn't representative.
    - **Route of Administration**: Extract the route(s) of drug administration for all drugs mentioned in the Recommended Dosage. If multiple drugs in a combination have different routes, combine them into a single string using the "+" sign to separate each drug and its route in parentheses (e.g., "Oral (IBRANCE) + IM (fulvestrant)"). Ensure that the route for each component of a combination therapy is explicitly stated if available in the text. If only one route is mentioned for the primary drug and the routes of combination drugs are unclear, provide the known route for the primary drug (e.g., "Oral (IBRANCE)").
    - **Treatment is based on**: Specify if the treatment duration/cycles are based on factors like response, progression, or a fixed schedule. If recommeded dosage contain weight (80kg, <80kg , >70kg etc.) then response should be "Weight based".
    - **Notes**: Any additional relevant information or specific instructions regarding dosage or administration.
    - **Medications**: If the drug is used in combination, list the names of the other medications involved (e.g., 'aromatase inhibitor or fulvestrant'). If no combination, use 'N/A'.
    - Extract the Patient group whether adult or pediatric.

    **NOTES**
    1. Don't provide any other information apart from JSON output.
    2. Use the information provided in below context only. If the context doesn't provide the relavant information then mention as N/A in the particular key of JSON output.
    3. Don't add Total Dosage Estimate in JSON output. It's just for calculating Yealry Dosage.
    4. If weight based dosage is found then split it based on weight in different objects of Dosage_Scenario.
    5. Output should be in valid json, array of objects format. Don't provide any additional text in output. Provide only JSON list with objects.


    Provide the extracted details in the following valid JSON structure (list of objects):
    ```
    [{"Indication":,"Dosage_Scenario": [{"Recommended Dosage":, "Cycle Duration (Weeks)":, "Max Cycles":, "Max Duration (Weeks)":, "Set Duration Under 1 Year":, "Treatment is based on":, "Notes":, "Medications":, "Yearly Dosage":, "Doses per Year":, "Initial Year Dosage":, "Patient Group":, "Pediatric Patient":, "Flat Dose":, "Dosing Segment Used":, "Route of Administration": }]}]
    ```

    Example of output:
    [
        {  "Indications": ,
            "Dosage_Scenario": [
                {
                    "Recommended Dosage": "For ≥45kg: 28 mcg/day",
                    "Cycle Duration (Weeks)":,
                    "Max Cycles":,
                    "Max Duration (Weeks)":,
                    "Set Duration Under 1 Year":,
                    "Treatment is based on":,
                    "Notes":,
                    "Medications":,
                    "Yearly Dosage":,
                    "Doses per Year":,
                    "Initial Year Dosage":,
                    "Patient Group":,
                    "Pediatric Patient":,
                    "Flat Dose":,
                    "Dosing Segment Used":,
                    "Route of Administration":
                }
                {
                    "Recommended Dosage": "For ≤45kg: 15 mcg/m2/day (not to exceed 28 mcg/day)",
                    "Cycle Duration (Weeks)":,
                    "Max Cycles":,
                    "Max Duration (Weeks)":,
                    "Set Duration Under 1 Year":,
                    "Treatment is based on":,
                    "Notes":,
                    "Medications":,
                    "Yearly Dosage":,
                    "Doses per Year":,
                    "Initial Year Dosage":,
                    "Patient Group":,
                    "Pediatric Patient":,
                    "Flat Dose":,
                    "Dosing Segment Used":,
                    "Route of Administration":
                }
            ]
        }
    ]
"""
    #print (prompt)

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

    # Send the POST request to the API
    response = requests.post(
    url=iliad_url+"/api/v1/chat/claude-3.5-sonnet",
    json={"messages": messages, "temperature": 0},
    headers={
        "Authorization": AUTH_TOKEN,
        'Accept': 'application/img',
        'Content-Type': 'application/json'
    },
    timeout=120  # Set a timeout for the request  # this is taking unusually longer. change to 20
    )
    response.raise_for_status()  # Raise an exception for HTTP errorslogger.info("Having the http response from img_json_data_1 ")
    #logger.info("Having the http response from img_json_data_1 ")
    #logger.info(f"Index: {index}, HTTP Status Code: {response.status_code}") #, Response Text: {response.text}")  # Log the HTTP response

    response.json()['completion']['content']

    markup1 = response.json()['completion']['content']
    #print(markup1)
    print("dosage details prompt output."+ "-"*10)
    print(markup1)
    return markup1

def extract_clinical_trial_in_tabular_format(Clinical_trial_details):
    all_rows = []

    for i in range(len(Clinical_trial_details)):
        print (i)
        #####print(Clinical_trial_details.loc[i,'Parsed'])
        article_indication = Clinical_trial_details.loc[i,'Parsed']
        drug_name = Clinical_trial_details.loc[i,'Drug_name']
        ndc_code = Clinical_trial_details.loc[i,'NDC']

        if drug_name == "Drug not found":
            continue

        json_match = re.search(r'(\[.*\])', article_indication, re.DOTALL)
        if not json_match:
            print(f"No JSON found for index {i}")
            continue

        try:
            final_data = json.loads(json_match.group(1))
        except json.JSONDecodeError as e:
            print(f"JSON Error at index {i}: {e}")
            print(f"Problematic data: {json_match.group(1)}")
            continue

        for data in final_data:
            indication = data.get('indication', '# Not available')
            for feature in data.get('features', []):
                all_rows.append({
                    'Drug': drug_name,
                    'NDC_Code': ndc_code,
                    'Indication': indication,
                    'Clinical Study': feature.get('study ID', '# Not available'),
                    'Dosage': feature.get('dosage', '# Not available'),
                    'Patient Selection': feature.get('Patient_selection', '# Not available'),
                    'Sub-Indication': feature.get('sub-indication', '# Not available'),
                    'Median duration of treatment': feature.get('Median duration of response', '# Not available'),
                })
    final_df = pd.DataFrame(all_rows)
    final_df.to_excel("outputs/Clinical_details_added_fields_09_May_25_merged.xlsx", index=False)
    return final_df

def extract_dosage_administration_in_tabular_format(final_data):
    Final_dosage_administration_details = pd.DataFrame()
    all_rows = []
    # for i in range(len(result)):
    #     print(i)
        # article_indication = result.loc[i, 'Parsed']
        # drug_name = result.loc[i, 'Drug_name']
        # ndc_code = result.loc[i, 'NDC']

        # if drug_name != "Drug not found":
        # json_match = re.search(r'(\[.*\])', article_indication, re.DOTALL)
        # if json_match:
        #     try:
        #         final_data = json.loads(json_match.group(1))
    if isinstance(final_data, str):
        print("D&A data is string")
        try:
            final_data = json.loads(final_data)
        except json.JSONDecodeError:
            print("Invalid JSON string")
    for data in final_data:
        base_row = {
            # 'NDC_Code': ndc_code,
            # 'Drug': drug_name,
            'Dosage Indication': data.get('Indication', '# Not available')
        }
        scenarios = data.get("Dosage_Scenario", [])
        if isinstance(scenarios, dict):
            scenarios = [scenarios]
        if isinstance(scenarios, list) and len(scenarios) > 0:
            for scenario in scenarios:
                row = base_row.copy()

                row['Recommended Dosage']= html.unescape(scenario.get('Recommended Dosage', '# Not available'))
                row['Cycle Duration (Weeks)']= scenario.get('Cycle Duration (Weeks)', '# Not available')
                row['Max Cycles']= scenario.get('Max Cycles', '# Not available')
                row['Max Duration (Weeks)']= scenario.get('Max Duration (Weeks)', '# Not available')
                row['Set Duration Under 1 Year']= scenario.get('Set Duration Under 1 Year', '# Not available')
                row['Treatment is based on']= scenario.get('Treatment is based on', '# Not available')
                row['Medications']= scenario.get('Medications', '# Not available')
                row['Yearly Dosage']= scenario.get('Yearly Dosage', '# Not available')
                row['Doses per Year']= scenario.get('Doses per Year', '# Not available')
                row['Initial Year Dosage']= scenario.get('Initial Year Dosage', '# Not available')
                row['Patient Group']= scenario.get('Patient Group', '# Not available')
                row['Pediatric Patient']= scenario.get('Pediatric Patient', '# Not available')
                row['Flat Dose']= scenario.get('Flat Dose', '# Not available')
                row['Dosing Segment Used']= scenario.get('Dosing Segment Used', '# Not available')
                row['Route of Administration']= scenario.get('Route of Administration', '# Not available')
                all_rows.append(row)
        else:

            base_row['Recommended Dosage']= html.unescape(data.get('Recommended Dosage', '# Not available'))
            base_row['Cycle Duration (Weeks)']= data.get('Cycle Duration (Weeks)', '# Not available')
            base_row['Max Cycles']= data.get('Max Cycles', '# Not available')
            base_row['Max Duration (Weeks)']= data.get('Max Duration (Weeks)', '# Not available')
            base_row['Set Duration Under 1 Year']= data.get('Set Duration Under 1 Year', '# Not available')
            base_row['Treatment is based on']= data.get('Treatment is based on', '# Not available')
            base_row['Medications']= data.get('Medications', '# Not available')
            base_row['Yearly Dosage']= data.get('Yearly Dosage', '# Not available')
            base_row['Doses per Year']= data.get('Doses per Year', '# Not available')
            base_row['Initial Year Dosage']= scenario.get('Initial Year Dosage', '# Not available')
            base_row['Patient Group']= data.get('Patient Group', '# Not available')
            base_row['Pediatric Patient']= data.get('Pediatric Patient', '# Not available')
            base_row['Flat Dose']= data.get('Flat Dose', '# Not available')
            base_row['Dosing Segment Used']= data.get('Dosing Segment Used', '# Not available')
            base_row['Route of Administration']= data.get('Route of Administration', '# Not available')
            all_rows.append(base_row)

    # except json.JSONDecodeError as e:
    #     print(f"JSON Error for NDC {ndc_code}: {e}")
    #     print(f"Problematic JSON data: {json_match.group(1)}")
    # else:
    # print(f"No JSON data found for NDC {ndc_code}.")

    new_df = pd.DataFrame(all_rows)
    Final_dosage_administration_details = pd.concat([Final_dosage_administration_details, new_df], ignore_index=True)
    Final_dosage_administration_details_unique = Final_dosage_administration_details.drop_duplicates()
    # Final_dosage_administration_details_unique.to_excel("outputs/Final_dosage_administration_details_May15_25.xlsx")
    return Final_dosage_administration_details_unique

if __name__ == "__main__":
    # NDCParsed = pd.read_excel("NDC_Dosage.xlsx")

    # NDCParsed = pd.DataFrame(NDCParsed)
    # NDCParsed.count()

    start_time = time.perf_counter()
    AUTH_TOKEN = get_chat_access_token()
    Final_Dosage_details=pd.DataFrame()
    Final_Dosage_details_updated=pd.DataFrame()
    df = pd.read_excel("inputs/test_input_json.xlsx")
    NDCParsed = df[['NDC']].drop_duplicates(subset='NDC').dropna().reset_index(drop=True)
    print(NDCParsed)
    d = {'NDC': [], 'Parsed': [], 'Index': [], 'error': []}
    e = {'NDC': [], 'Function': [], 'Indication Index': [], 'error': []}
    error_log_file=pd.DataFrame(data=e)
    failed_adverse_reactions_details = pd.DataFrame(data=d)
    Drug_details,dosage_administration_details,all_adverse_reactions_details_df = Populating_Required_details(NDCParsed,ndc_url)
    # extract_clinical_trial_in_tabular_format(Clinical_trial_details)
    # print("clinical details are stored")
    print(f"len of drug details before regex match: {len(Drug_details)}")
    Final_Drug_details=extraction_in_tabular_format(Drug_details)
    #dosage_administration_details.to_csv("dosage_administration_details.csv")
    # Final_Drug_details.dropna(inplace = True)
    # print(Final_Drug_details)
    if Final_Drug_details is not None and not Final_Drug_details.empty:
        Final_Drug_details.dropna(inplace=True)
    else:
        print("Final_Drug_details is None or empty.")
    Final_Drug_details = Final_Drug_details.reset_index()


    for i in range(len(Final_Drug_details)):
    #for i in range(4):
        print("looping over indication/Drug details indications")
        Ndc=Final_Drug_details.loc[i,'NDC']
        print(Ndc)
        dosage = dosage_administration_details[dosage_administration_details["NDC"] == Final_Drug_details.loc[i,'NDC']]
        # clinical_details=Clinical_trial_details[Clinical_trial_details["NDC"] == Final_Drug_details.loc[i,'NDC']]
        all_adverse_reactions_details = all_adverse_reactions_details_df[all_adverse_reactions_details_df["NDC"] == Final_Drug_details.loc[i,'NDC']]

        dosage = dosage.reset_index()
        # clinical_details=clinical_details.reset_index()
        all_adverse_reactions_details=all_adverse_reactions_details.reset_index()

        #dosage.to_csv("dosage.csv")
        #print(dosage.loc[0,'Parsed'])
        #print(dosage)
        dosage_val = dosage.loc[0,'Parsed']
        if all_adverse_reactions_details is not None and not all_adverse_reactions_details.empty and 'Parsed' in all_adverse_reactions_details.columns and 0 in all_adverse_reactions_details.index:
            adverse_reactions_val = all_adverse_reactions_details.loc[0, 'Parsed']
        else:
            failed_adverse_reactions_details.loc[i] = [Ndc,all_adverse_reactions_details,'None',"clinical_details is none or empty or 0 index is not present or Parsed is not present"]
        # clinical_val=clinical_details.loc[0,'Parsed']
        # if clinical_details is not None and not clinical_details.empty and 'Parsed' in clinical_details.columns and 0 in clinical_details.index:
        #     clinical_val = clinical_details.loc[0, 'Parsed']
        # elif clinical_details is not None and not clinical_details.empty:
        #     print("clinical_details is none or empty")
        #     clinical_val = None  # or some fallback value
        #     failed_clinical_details.loc[i] = [Ndc,'None','None',"clinical_details is none or empty"]
        # elif 0 not in clinical_details.index:
        #     print("0 index is not present")
        #     clinical_val = None
        #     failed_clinical_details.loc[i] = [Ndc,clinical_details,'None',"0 index is not present"]
        # elif 'Parsed' not in clinical_details.columns:
        #     print("Parsed is not present")
        #     clinical_val = None
        #     failed_clinical_details.loc[i] = [Ndc,'None','None',"Parsed is not present"]
        # print(clinical_details.head(2))
        # print("Columns:", clinical_details.columns.tolist())

        condition = Final_Drug_details.loc[i,'Indication']
        subcondition = Final_Drug_details.loc[i,'Subindications']
        Usage = Final_Drug_details.loc[i,'Usage']
        Drug_name = Final_Drug_details.loc[i,'Drug_name']
        LineofTherapy = Final_Drug_details.loc[i,'LineofTherapy']
        TypeofTherapy = Final_Drug_details.loc[i,'TypeofTherapy']
        Combinationdrug = Final_Drug_details.loc[i,'Combinationdrug']
        Disease_full = Final_Drug_details.loc[i,'Disease_full']
        Stringp = "Drug name:" + Drug_name + "for disease_full:" + str(Disease_full) + " for indication:" + str(condition) + "for subIndication:" + str(subcondition) + "for usage:" + str(Usage) + " for lineofTherapy:" + str(LineofTherapy) + "for typeofTherapy:" + str(TypeofTherapy) + "for combinationdrug:" + str(Combinationdrug)

        #print(Stringp)
        #print(dosage_text)
        Dosage_json = extracting_dosage_details(Stringp, str(dosage_val).strip().replace("   ", ""))
        # print(Dosage_json)
        # extracted_clinical_details = extracting_clinicalTrial_details(Stringp, str(clinical_val).strip().replace("   ", ""))
        try:
            extracted_adverse_reaction_details=extracting_adverse_reaction_details(Stringp, str(adverse_reactions_val).strip().replace("   ", ""))
            # print(f"extracted_adverse_reaction_details--- {extracted_adverse_reaction_details}")
            json_match = re.search(r'(\[.*\])', Dosage_json, re.DOTALL)
            if json_match:













                json_data = json_match.group(1) #group(1) the () content is considered irrespective of other text in re search
                print(f"dosage json data:-- {json_data}")
                dosage_fixed = repair_json(json_data)


                try:





                    dosage_data = json.loads(dosage_fixed)
                    dosage_data= extract_dosage_administration_in_tabular_format(dosage_data)
                except Exception as e:
                    error=f"[{i}] Failed in extract_dosage_administration_in_tabular_format: {e}"
                    print(error)

                    error_log_file = pd.concat([error_log_file, pd.DataFrame([{
                        "NDC": Ndc,
                        "Function": "extract_dosage_administration_in_tabular_format",
                        "Indication Index": i,
                        # "error": str(e),
                        "error": error
                    }])], ignore_index=True)


                # df = pd.json_normalize(dosage_data)
                df_dosage=dosage_data

                # clinical_match = re.search(r'(\[.*\])', extracted_clinical_details, re.DOTALL)
                # clinical_match = re.search(r'(\[.*\])', extracted_clinical_details, re.DOTALL)
                adverse_reaction_match = re.search(r'(\[.*\])', extracted_adverse_reaction_details, re.DOTALL)
                adverse_reaction_data=[]
                if adverse_reaction_match:
                    try:
                        json_data = adverse_reaction_match.group(1).strip()
                        print("adverse reactions Raw JSON string:", repr(json_data))

                        # Try loading it
                        fixed = repair_json(json_data)
                        adverse_reaction_data = json.loads(fixed)
                        if isinstance(adverse_reaction_data, dict):
                            adverse_reaction_data = [adverse_reaction_data]
                        df_adverse_reaction = pd.json_normalize(adverse_reaction_data)
                        df_adverse_reaction = df_adverse_reaction.add_prefix('adverse_reaction_')
                        # Ensure both dfs have the same number of rows by repeating the shorter one
                        max_len = max(len(df_dosage), len(df_adverse_reaction))

                        # # Repeat rows if needed
                        # if len(df_dosage) < max_len:
                        #     df_dosage = pd.concat([df_dosage] * ((max_len // len(df_dosage)) + 1), ignore_index=True).iloc[:max_len]








                        # if len(df_adverse_reaction) < max_len:
                        #     df_adverse_reaction = pd.concat([df_adverse_reaction] * ((max_len // len(df_adverse_reaction)) + 1), ignore_index=True).iloc[:max_len]



                        # # Now concatenate


                        # df_combined = pd.concat([df_dosage, df_adverse_reaction], axis=1)
                        # Perform cross join
                        df_combined = df_dosage.merge(df_adverse_reaction, how="cross")

                    except json.JSONDecodeError as e:
                        print(f"Failed to decode clinical JSON for NDC: {Ndc}")
                        print(f"Error: {e}")
                        print("Offending JSON string:", repr(json_data))
                        print("Trying fallback with multiple arrays...")
                        failed_adverse_reactions_details.loc[i] = [Ndc, json_data, i, f"JSONDecodeError: {e}"]
                        matches = re.findall(r'\[.*?\]', extracted_adverse_reaction_details, re.DOTALL)

                        for match in matches:
                            try:
                                fixed = repair_json(match)
                                adverse_reaction_data.extend(json.loads(fixed))
                            except Exception as inner_e:
                                print(f"Failed sub-array: {inner_e}")
                                failed_adverse_reactions_details.loc[i] = [Ndc, match, i, f"Repair/Decode Error: {inner_e}"]
                                error_log_file = pd.concat([error_log_file, pd.DataFrame([{
                                                    "NDC": Ndc,
                                                    "Function": "non-graady regex",
                                                    "Indication Index": i,
                                                    # "error": str(e),
                                                    "error": inner_e
                                                }])], ignore_index=True)

                        if adverse_reaction_data:
                            df_adverse_reaction = pd.json_normalize(adverse_reaction_data)
                            df_adverse_reaction = df_adverse_reaction.add_prefix('adverse_reactions_')
                            # df_combined = pd.concat([df_dosage, df_adverse_reaction], axis=1)
                            # Perform cross join
                            df_combined = df_dosage.merge(df_adverse_reaction, how="cross")
                        else:
                            df_combined = df_dosage

            else:
                print("No valid JSON array found in clinical details.")
                failed_adverse_reactions_details.loc[i] = [Ndc, extracted_adverse_reaction_details, i, "No JSON array found"]
                df_combined = df_dosage
        except Exception as e:
            error=f"[{i}] Failed in outer try block: {e}"
            print(error)
            error_log_file = pd.concat([error_log_file, pd.DataFrame([{
                        "NDC": Ndc,
                        "Function": "outer try block",
                        "Indication Index": i,
                        # "error": str(e),
                        "error": error
                    }])], ignore_index=True)

        #data = json.loads(article_indication)

    # Convert JSON data to a pandas DataFrame

        row_dict = Final_Drug_details.iloc[i].to_dict()

    # Add the dictionary as new columns to df1
        for col, val in row_dict.items():
            df_combined[col] = str(val)
        # df['NDC'] = Final_Drug_details.loc[i,'NDC']
        # df['Drug_name'] = Final_Drug_details.loc[i,'Drug_name']
        # df['Indication'] = Final_Drug_details.loc[i,'Indication']
        # df['Subindications'] = Final_Drug_details.loc[i,'Subindications']
        # df['Usage'] = Final_Drug_details.loc[i,'Usage']

        if i == 0:
            Final_Dosage_details = pd.concat([Final_Dosage_details, df_combined], ignore_index=True)
            Final_Dosage_details_updated= Final_Dosage_details
            Final_Dosage_details_updated['Derived duration of treatment'] = Final_Dosage_details_updated.apply(
                                                                            lambda row: row['adverse_reaction_Max duration of treatment (weeks)'] if row['Max Duration (Weeks)'] == 'N/A' else row['Max Duration (Weeks)'],
                                                                            axis=1
                                                                        )
            Final_Dosage_details_updated['Derived Set Duration Under 1 Year'] = Final_Dosage_details_updated.apply(
                                                                                lambda row: "No" if row['Derived duration of treatment'] == 'N/A'
                                                                                or pd.to_numeric(row['Derived duration of treatment'], errors='coerce') < 52 else "Yes",
                                                                                axis=1
                                                                            )

            # Final_Dosage_details_updated = pd.concat([Final_Dosage_details_updated, df_combined], ignore_index=True)

        else:
            Final_Dosage_details = pd.concat([Final_Dosage_details, df_combined], ignore_index=True)

            #Final_Dosage_details.to_excel("outputs/Final_Dosage_details_NDCs_20_May_2.xlsx")
            Final_Dosage_details_updated= Final_Dosage_details
            Final_Dosage_details_updated['Derived duration of treatment'] = Final_Dosage_details_updated.apply(
                                                                            lambda row: row['adverse_reaction_Max duration of treatment (weeks)'] if row['Max Duration (Weeks)'] == 'N/A' else row['Max Duration (Weeks)'],
                                                                            axis=1
                                                                        )
            Final_Dosage_details_updated['Derived Set Duration Under 1 Year'] = Final_Dosage_details_updated.apply(
                                                                                lambda row: "No" if row['Derived duration of treatment'] == 'N/A'
                                                                                or pd.to_numeric(row['Derived duration of treatment'], errors='coerce') < 52 else "Yes",
                                                                                axis=1
                                                                            )
            # Final_Dosage_details_updated = pd.concat([Final_Dosage_details_updated, df_combined], ignore_index=True)
            Final_Dosage_details_updated.to_excel("outputs/Final_Dosage_details_updated_21_May_1.xlsx")
            # Final_Dosage_details_updated=Final_Dosage_details
    # Final_Dosage_details.to_excel("outputs/Final_Dosage_details2_NDCs5_16_May_252.xlsx")
    Final_Dosage_details_updated.to_excel("outputs/Final_Dosage_details_updated2_final_21_May_1.xlsx")
    #failed_adverse_reactions_details.to_excel("outputs/failed_clinical_details_20_may_25.xlsx")
    #error_log_file.to_excel("outputs/error_log_file_NDCs_19_may_25.xlsx")
    end_time = time.perf_counter()
    print(f"Total execution time: {end_time - start_time:.2f} seconds")