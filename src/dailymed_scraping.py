# dailymed_scraping.py

import math
import re
import logging
from typing import Optional
import asyncio
import aiohttp
from bs4 import BeautifulSoup
import pandas as pd
from definition import NDC
import time

def get_set_id(soup: BeautifulSoup)-> Optional[str]:
    """Extracts set_id given the BeatifulSoup of the webpage

    Args:
        soup (BeautifulSoup): BeatifulSoup object of the webpage
    Returns:
        Optional[str]: set_id if found else None
    """
    set_id_tag = soup.find("a", href=re.compile(r"setid="))
    set_id = None
    if set_id_tag:
        set_id = re.search(r"setid=([a-f0-9\-]+)", set_id_tag["href"]).group(1)

    return set_id


def extract_label(soup: BeautifulSoup)-> Optional[str]:
    """Tries to find the title from BeautifulSoup object
    returns None if not found

    Args:
        soup (BeautifulSoup): BeautifulSoup object of the webpage

    Returns:
        Optional[str]: returns Label if found else None
    """
    long_title_tag = soup.find("h3", class_="long-title")
    if long_title_tag:
        return long_title_tag.get_text()
    return None


def finding_other_ndcs(soup: BeautifulSoup)->list:
    """Tries to find out related NDCs from a table

    Args:
        soup (BeautifulSoup): BeautifulSoup object of the webpage

    Returns:
        list: list of all related NDCs
    """
    NDC_pttrn = r"\d{4,5}\-\d{3,4}-\d{1,2}"
    modal_table = soup.find_all("table", class_="modal")
    related_NDCs = []
    for m_table in modal_table:
        if m_table.find("th", string=lambda text: text and "NDC" in text):
            rows = m_table.find_all("tr")
            for row in rows:
                for item in row.find_all("td"):
                    m = re.findall(NDC_pttrn, item.getText())
                    if m:
                        related_NDCs.append(m[0])

    return related_NDCs

def extract_NDC_fields(html, ndc):
    soup = BeautifulSoup(html, "html.parser")
    set_id = get_set_id(soup)
    if set_id is None:
        logging.warning(f"set_id not found for {ndc}")
    label = extract_label(soup)
    if label is None:
        logging.warning(f"label not found for {ndc}")

    related_ndcs = finding_other_ndcs(soup)

    related_ndcs_cleaned = []
    data_rows = []
    for rel_ndc  in related_ndcs:
        try:
            r_ndc_obj = NDC(rel_ndc)
        except AssertionError as e:
            logging.exception(e)
            continue
        related_ndcs_cleaned.append(r_ndc_obj)
        row_data = {
            "NDC": ndc.ndc,
            "set_id": set_id,
            "related_ndcs": r_ndc_obj.ndc,
            # "related_ndcs_cleaned": related_ndcs_cleaned_str,
            "label": label
        }
        data_rows.append(row_data)

    new_rows = pd.DataFrame(

        data_rows
    )
    return new_rows, set_id, label

MAX_CONCURRENT_REQUESTS = 10

semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

async def fetch_ndc_details(session, ndc, max_retries=3):
    existing_ndc_rows = ndc_data[ndc_data["related_ndcs"].str.contains(ndc.ndc)]
    if len(existing_ndc_rows) == 0:
        url = f"https://dailymed.nlm.nih.gov/dailymed/search.cfm?query={ndc}"
        for attempt in range(max_retries):
            try:
                async with session.get(url, timeout = aiohttp.ClientTimeout(total=40)) as response:
                    if response.status == 200:
                        html = await response.text()
                        data, set_id, label = extract_NDC_fields(html, ndc)
                        return data
                    elif set_id is None or label is None:
                        logging.warning(f"No result content for {ndc}")
                        return None
                    else:
                        logging.warning(f"Request failed with status {response.status} for {ndc}")
                        # print("Failed for {ndc}")
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionResetError) as e:
                logging.warning(f"Attempt {attempt+1} failed for {ndc} with error: {e}")
                await asyncio.sleep(1.5 * (attempt + 1))  # Exponential backoff
            except Exception as e:
                logging.exception(f"Unexpected error for {ndc}: {e}")
                return None
        return None


async def scrap_NDC_details(ndc_list):
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_ndc_details(session, ndc) for ndc in ndc_list]
        results = await asyncio.gather(*tasks)
        return [res for res in results if res is not None and len(res) > 0]


async def main():
    global ndc_data
    ndc_data = pd.DataFrame(
        columns=[
            "NDC",
            "set_id",
            "related_ndcs",
            # "related_ndcs_cleaned",
            "label"
            ],
        dtype = str
        )


    batch_size = 200

    # input list of the NDCs
    # ndcs_to_parse = [
    #     "70255002503",
    #     "00006302602",
    #     "55513048824",
    #     "70255001002",
    #     "00003373413",
    #     "00002534060",
    #     "50419017101",
    #     "00069019730",
    #     "00003232711"
    # ]

    # Load NDCs from Excel
    NDCParsed = pd.read_excel("input/AnalySource_Report.xlsx")

    NDCParsed = pd.DataFrame(NDCParsed)

    ndc_column = None
    for col in NDCParsed.columns:
        if 'ndc' in str(col).strip().lower():
            ndc_column = col
            break

    if ndc_column:
        NDC_df = NDCParsed[ndc_column].dropna().drop_duplicates()  #removes NA and converts to list
        NDC_list=NDC_df.to_list()

    else:
        print("No column containing 'NDC' was found in the Excel file.")

    ndcs_to_parse_objs = [NDC(ndc_str) for ndc_str in NDC_list]

    batch_cnt = math.ceil(len(ndcs_to_parse_objs) / batch_size)
    for b_idx in range(batch_cnt):
        batch_results = await scrap_NDC_details(ndcs_to_parse_objs[b_idx * batch_size : (b_idx + 1)* batch_size])

        if batch_results:
            batch_df = pd.concat(batch_results, ignore_index=True)
            # batch_df = batch_df.drop_duplicates(subset=["related_ndcs"], keep="first")

        ndc_data = pd.concat([ndc_data, batch_df], ignore_index=True)

    ndc_data.to_excel("output/AnalySourceNDCs_Async.xlsx", index=False)

if __name__ == "__main__":
    st = time.perf_counter()
    asyncio.run(main())
    ed = time.perf_counter()
    print(f"Time Taken: {ed - st}")