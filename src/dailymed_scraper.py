"""
DailyMed Web Scraper Module

This module handles fetching and parsing drug label data from DailyMed.gov
using async HTTP requests with retry logic and rate limiting.

Functions:
    get_set_id: Extract set_id from DailyMed page
    extract_label: Extract drug label/title from page
    find_related_ndcs: Find related NDCs from modal tables
    extract_ndc_fields: Parse NDC fields from HTML
    fetch_ndc_details: Async function to fetch NDC details with retries
    scrape_ndc_details: Main async function to batch-process NDCs
"""

import logging
import re
import asyncio
import aiohttp
from typing import Optional, List, Tuple
from bs4 import BeautifulSoup
import pandas as pd
import time

# Configure logging
logger = logging.getLogger(__name__)

# Constants
DAILYMED_SEARCH_URL = "https://dailymed.nlm.nih.gov/dailymed/search.cfm?labeltype=human&query="
NDC_PATTERN = r"\d{4,5}\-\d{3,4}-\d{1,2}"
MAX_CONCURRENT_REQUESTS = 10
REQUEST_TIMEOUT = 40  # seconds
MAX_RETRIES = 3
RETRY_BACKOFF_FACTOR = 1.5


def get_set_id(soup: BeautifulSoup) -> Optional[str]:
    """
    Extract the set_id from a DailyMed page.
    
    The set_id uniquely identifies a drug label revision in DailyMed's system.
    
    Args:
        soup (BeautifulSoup): Parsed HTML of the DailyMed page
        
    Returns:
        Optional[str]: The set_id if found, None otherwise
        
    Example:
        >>> soup = BeautifulSoup(html, 'html.parser')
        >>> set_id = get_set_id(soup)
        >>> print(set_id)
        'a1b2c3d4-e5f6-7890-abcd-ef1234567890'
    """
    try:
        set_id_tag = soup.find("a", href=re.compile(r"setid="))
        if set_id_tag:
            match = re.search(r"setid=([a-f0-9\-]+)", set_id_tag["href"])
            if match:
                return match.group(1)
    except Exception as e:
        logger.warning(f"Error extracting set_id: {e}")
    
    return None


def extract_label(soup: BeautifulSoup) -> Optional[str]:
    """
    Extract the drug label/title from a DailyMed page.
    
    Args:
        soup (BeautifulSoup): Parsed HTML of the DailyMed page
        
    Returns:
        Optional[str]: The drug label if found, None otherwise
        
    Example:
        >>> soup = BeautifulSoup(html, 'html.parser')
        >>> label = extract_label(soup)
        >>> print(label)
        'OPDIVO® (nivolumab) injection'
    """
    try:
        long_title_tag = soup.find("h3", class_="long-title")
        if long_title_tag:
            return long_title_tag.get_text(strip=True)
    except Exception as e:
        logger.warning(f"Error extracting label: {e}")
    
    return None


def find_related_ndcs(soup: BeautifulSoup) -> List[str]:
    """
    Extract related/alternate NDCs from modal tables in the page.
    
    DailyMed pages often list alternative package sizes and strengths
    as related NDCs in modal tables.
    
    Args:
        soup (BeautifulSoup): Parsed HTML of the DailyMed page
        
    Returns:
        List[str]: List of related NDC strings found
        
    Example:
        >>> soup = BeautifulSoup(html, 'html.parser')
        >>> related_ndcs = find_related_ndcs(soup)
        >>> print(related_ndcs)
        ['70255-0025-03', '70255-0025-10']
    """
    related_ndcs = []
    
    try:
        modal_tables = soup.find_all("table", class_="modal")
        
        for modal_table in modal_tables:
            # Check if this table has an NDC column
            if not modal_table.find("th", string=lambda text: text and "NDC" in text):
                continue
            
            rows = modal_table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                for cell in cells:
                    # Find all NDC patterns in this cell
                    matches = re.findall(NDC_PATTERN, cell.get_text())
                    for match in matches:
                        if match not in related_ndcs:
                            related_ndcs.append(match)
    
    except Exception as e:
        logger.warning(f"Error finding related NDCs: {e}")
    
    return related_ndcs


def extract_ndc_fields(html: str, ndc: str) -> Tuple[pd.DataFrame, Optional[str], Optional[str]]:
    """
    Parse HTML content and extract NDC-related fields.
    
    This function extracts the set_id, label, and related NDCs from
    a DailyMed HTML page for a given NDC.
    
    Args:
        html (str): Raw HTML content from DailyMed
        ndc (str): The NDC being processed (for logging)
        
    Returns:
        Tuple containing:
            - pd.DataFrame: Dataframe with columns [NDC, set_id, related_ndcs, label]
            - Optional[str]: Extracted set_id
            - Optional[str]: Extracted label
            
    Example:
        >>> df, set_id, label = extract_ndc_fields(html_content, "70255002503")
        >>> print(df.shape)
        (2, 4)
    """
    soup = BeautifulSoup(html, "html.parser")
    
    set_id = get_set_id(soup)
    if not set_id:
        logger.warning(f"set_id not found for NDC {ndc}")
    
    label = extract_label(soup)
    if not label:
        logger.warning(f"label not found for NDC {ndc}")
    
    related_ndcs = find_related_ndcs(soup)
    
    # Create DataFrame rows
    data_rows = []
    if related_ndcs:
        for rel_ndc in related_ndcs:
            row_data = {
                "NDC": ndc,
                "set_id": set_id or "N/A",
                "related_ndcs": rel_ndc,
                "label": label or "N/A"
            }
            data_rows.append(row_data)
    else:
        # Even if no related NDCs, create one row with the original NDC
        row_data = {
            "NDC": ndc,
            "set_id": set_id or "N/A",
            "related_ndcs": ndc,
            "label": label or "N/A"
        }
        data_rows.append(row_data)
    
    df = pd.DataFrame(data_rows)
    return df, set_id, label


async def fetch_ndc_details(
    session: aiohttp.ClientSession,
    ndc: str,
    existing_df: pd.DataFrame = None,
    max_retries: int = MAX_RETRIES
) -> Optional[pd.DataFrame]:
    """
    Async function to fetch NDC details from DailyMed with exponential backoff retry.
    
    Args:
        session (aiohttp.ClientSession): Shared aiohttp session
        ndc (str): NDC code to fetch
        existing_df (pd.DataFrame): DataFrame of already-fetched NDCs (to avoid duplicates)
        max_retries (int): Number of retry attempts
        
    Returns:
        Optional[pd.DataFrame]: DataFrame with extracted NDC fields, or None on failure
        
    Raises:
        asyncio.TimeoutError: If request times out
        aiohttp.ClientError: On network errors
    """
    # Check if this NDC is already in existing data
    if existing_df is not None and "related_ndcs" in existing_df.columns:
        if ndc in existing_df["related_ndcs"].values:
            logger.info(f"NDC {ndc} already processed, skipping")
            return None
    
    url = f"{DAILYMED_SEARCH_URL}{ndc}"
    
    for attempt in range(max_retries):
        try:
            timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            async with session.get(url, timeout=timeout) as response:
                if response.status == 200:
                    html = await response.text()
                    df, set_id, label = extract_ndc_fields(html, ndc)
                    logger.info(f"Successfully fetched NDC {ndc}")
                    return df
                elif response.status == 404:
                    logger.warning(f"NDC {ndc} not found (404)")
                    return None
                else:
                    logger.warning(f"Unexpected status {response.status} for NDC {ndc}")
                    # Don't retry on unexpected status
                    return None
        
        except asyncio.TimeoutError:
            logger.warning(f"Timeout for NDC {ndc} (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                wait_time = REQUEST_TIMEOUT * (RETRY_BACKOFF_FACTOR ** attempt)
                await asyncio.sleep(wait_time)
        
        except aiohttp.ClientError as e:
            logger.warning(f"Client error for NDC {ndc}: {e} (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                wait_time = 2 * (RETRY_BACKOFF_FACTOR ** attempt)
                await asyncio.sleep(wait_time)
        
        except Exception as e:
            logger.exception(f"Unexpected error for NDC {ndc}: {e}")
            return None
    
    logger.error(f"Failed to fetch NDC {ndc} after {max_retries} retries")
    return None


async def scrape_ndc_details(
    ndc_list: List[str],
    batch_size: int = 100,
    existing_df: pd.DataFrame = None
) -> pd.DataFrame:
    """
    Main async function to scrape NDC details from DailyMed.
    
    Processes a list of NDCs in batches with controlled concurrency.
    
    Args:
        ndc_list (List[str]): List of NDC codes to scrape
        batch_size (int): Number of NDCs to process before combining results
        existing_df (pd.DataFrame): DataFrame of already-processed NDCs
        
    Returns:
        pd.DataFrame: Combined DataFrame with all extracted NDC fields
        
    Example:
        >>> ndcs = ["70255002503", "00069055402"]
        >>> df = asyncio.run(scrape_ndc_details(ndcs))
        >>> print(df.shape)
        (4, 4)
    """
    connector = aiohttp.TCPConnector(
        limit=MAX_CONCURRENT_REQUESTS,
        limit_per_host=5,
        ttl_dns_cache=300
    )
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT * 2)
    
    all_results = []
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # Process in batches
        for i in range(0, len(ndc_list), batch_size):
            batch = ndc_list[i : i + batch_size]
            logger.info(f"Processing batch {i // batch_size + 1}: NDCs {i + 1}-{min(i + batch_size, len(ndc_list))}")
            
            # Create semaphore for this batch
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
            
            async def fetch_with_semaphore(ndc):
                async with semaphore:
                    return await fetch_ndc_details(session, ndc, existing_df)
            
            # Run tasks concurrently
            tasks = [fetch_with_semaphore(ndc) for ndc in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            for result in results:
                if isinstance(result, pd.DataFrame) and not result.empty:
                    all_results.append(result)
                elif isinstance(result, Exception):
                    logger.exception(f"Task failed: {result}")
            
            # Rate limiting between batches
            await asyncio.sleep(1)
    
    # Combine all results
    if all_results:
        combined_df = pd.concat(all_results, ignore_index=True)
        # Remove duplicates based on NDC and related_ndcs
        combined_df = combined_df.drop_duplicates(subset=["NDC", "related_ndcs"], keep="last")
        return combined_df
    else:
        return pd.DataFrame(columns=["NDC", "set_id", "related_ndcs", "label"])


if __name__ == "__main__":
    # Configure logging for testing
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s:%(lineno)d - [%(levelname)s] %(message)s"
    )
    
    # Example: Scrape a list of NDCs
    test_ndcs = ["70255002503", "00069055402", "50242060301"]
    
    # Run async scraper
    df_results = asyncio.run(scrape_ndc_details(test_ndcs))
    print(f"\nScraped {len(df_results)} NDC records")
    print(df_results.head())
