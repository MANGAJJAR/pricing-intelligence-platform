# Generic name NDC Serach.py

from typing import Optional
import requests
from bs4 import BeautifulSoup
import time
import re
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def is_NDC(s):
    if not isinstance(s, str):
        return False # Ensure the input is a string
    if not s: # Handle empty string case
        return False

    for char in s:
        if not (char.isdigit() or char == '-'):
            return False
    return True

def if_NDC(row):
    raw_ndc = str(row).strip()
    parts = raw_ndc.split('-')
    if len(parts) == 3:
        NDCi = parts[0].zfill(5) + parts[1].zfill(4) + parts[2].zfill(2)
    else:
        NDCi = raw_ndc.replace('-', '').zfill(11)
    return NDCi

def convert_generic_name(generic_name_input: str) -> str:
    """
    Replaces spaces, hyphens, underscores, slashes, and other punctuation
    with a plus sign, maintaining words, and ensures no trailing plus sign.

    Args:
        generic_name_input (str): The input string to format.

    Returns:
        str: The formatted string with '+' between words.
    """
    # Replace all non-alphanumeric characters (except potentially spaces)
    # with a single space. This handles most punctuation and ensures words are separated.
    cleaned_string = re.sub(r'[^\w\s]', ' ', generic_name_input)

    # Strip leading/trailing whitespace after cleaning
    cleaned_string = cleaned_string.strip()

    # Replace any sequence of one or more whitespace characters with a single '+'
    formatted_string = re.sub(r'\s+', '+', cleaned_string)

    return formatted_string

def add_dailymed_prefix_if_not(url_string: str) -> str:
    """
    Adds "https://dailymed.nlm.nih.gov" as a prefix to a string if it doesn't already contain it.

    Args:
        url_string: The input string (presumably a URL or part of one).

    Returns:
        The modified string with the DailyMed prefix, or the original string if it already contained it.
    """
    prefix = "https://dailymed.nlm.nih.gov"
    if prefix not in url_string:
        return prefix + url_string
    else:
        return url_string


def extract_drug_labels(soup):
    """
    Extracts drug titles from a given URL.
    Titles are expected to be in <h2 class="drug-name long-title"> tags.
    Handles <br> tags by replacing them with newlines.

    Args:
        url (str): The URL of the webpage to scrape.

    Returns:
        list: A list of extracted and cleaned drug titles.
              Returns an empty list if no titles are found or an error occurs.
    """
    try:
        titles = []
        # Find all <h2> tags with the specified class
        h2_elements = soup.find_all('h2', class_='drug-name long-title')

        for h2 in h2_elements:
            # Get the content of the <a> tag within the <h2>
            # If there's no <a> tag, get the text directly from h2
            a_tag = h2.find('a', class_='drug-info-link')
            if a_tag:
                # Replace <br> tags with a newline character within the <a> tag's content
                for br in a_tag.find_all('br'):
                    br.replace_with('\n')
                # Get the cleaned text
                title_text = a_tag.get_text(separator='\n', strip=True)
            else:
                # If no <a> tag, get text directly from h2 and clean it
                for br in h2.find_all('br'):
                    br.replace_with('\n')
                title_text = h2.get_text(separator='\n', strip=True)

            titles.append(title_text)
        return titles
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching the URL: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"An unexpected error occurred in extract_drug_labels: {e}", exc_info=True)
        return []

def extract_ndc_codes(soup):
    """
    Extracts all NDC codes from a given URL.
    NDC codes are expected to be within <span class="ndc-codes"> tags,
    and may include codes hidden within <div class="more-codes"> inside.

    Args:
        url (str): The URL of the webpage to scrape.

    Returns:
        list: A list of lists, where each inner list contains the NDC codes
              found within a single 'ndc-codes' span element.
              Returns an empty list if no NDCs are found or an error occurs.
    """
    try:
        all_ndc_lists = []
        # Find all <span> tags with the specified class
        ndc_span_elements = soup.find_all('span', class_='ndc-codes')

        for ndc_span in ndc_span_elements:
            current_ndc_list = []

            # Get all text content from the span, excluding the 'view more' link itself
            # We'll then parse this text for NDC patterns
            # First, handle the visible NDCs
            # We need to remove the 'view more' link temporarily to get clean text
            view_more_link = ndc_span.find('a', class_='view-more')
            if view_more_link:
                view_more_link.extract() # Remove the link from the current span for text extraction

            # Get all text content from the span (excluding the .more-codes div initially)
            # This captures the directly visible NDCs
            visible_text = ""
            for content in ndc_span.contents:
                if content.name == 'div' and 'more-codes' in content.get('class', []):
                    # Skip the 'more-codes' div for now, we'll process it separately
                    continue
                if isinstance(content, str):
                    visible_text += content
                elif content.name: # It's a tag, get its text
                    visible_text += content.get_text()

            # Find all NDC patterns (e.g., XXXYY-ZZZ-AA) in the visible text
            # NDC patterns can vary, this regex is a common format. Adjust if needed.
            # It matches 5 digits, hyphen, 3-4 digits, hyphen, 1-2 digits.
            ndc_pattern = re.compile(r'\b\d{4,5}-\d{3,4}-\d{1,2}\b')

            found_visible_ndcs = ndc_pattern.findall(visible_text)
            current_ndc_list.extend(found_visible_ndcs)

            # Now, handle the 'more-codes' div if it exists
            more_codes_div = ndc_span.find('div', class_='more-codes')
            if more_codes_div:
                more_codes_text = more_codes_div.get_text(separator=' ', strip=True)
                found_hidden_ndcs = ndc_pattern.findall(more_codes_text)
                current_ndc_list.extend(found_hidden_ndcs)

            # Clean up the list: remove duplicates and sort for consistency (optional)
            current_ndc_list = sorted(list(set(current_ndc_list)))

            if current_ndc_list: # Only add if there are actual NDCs found
                all_ndc_lists.append(current_ndc_list)

        return all_ndc_lists
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching the URL: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"An unexpected error occurred in extract_ndc_codes: {e}", exc_info=True)
        return []

def extract_packagers(soup):
    """
    Extracts packager names from a given URL.
    Packager names are expected to be within <span> tags,
    inside <li> tags, which are themselves inside <ul class="drug-information">.
    The <li> typically contains a <strong>Packager:</strong> element.

    Args:
        url (str): The URL of the webpage to scrape.

    Returns:
        list: A list of extracted packager names.
              Returns an empty list if no packagers are found or an error occurs.
    """
    try:
        packagers = []

        # Find all <ul> tags with the specified class
        drug_info_uls = soup.find_all('ul', class_='drug-information')

        for ul in drug_info_uls:
            # Find all <li> tags within the current <ul>
            list_items = ul.find_all('li')

            for li in list_items:
                # Check if the <li> contains a <strong> tag with "Packager:" text
                strong_tag = li.find('strong')
                if strong_tag and 'Packager:' in strong_tag.get_text(strip=True):
                    # If it's the "Packager:" list item, find the immediate <span> sibling
                    # or the <span> directly within the <li>
                    span_tag = li.find('span')
                    if span_tag:
                        packager_name = span_tag.get_text(strip=True)
                        if packager_name: # Ensure the extracted name is not empty
                            packagers.append(packager_name)

        # Remove duplicates and return a sorted list for consistency (optional)
        return packagers

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching the URL: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"An unexpected error occurred in extract_packagers: {e}", exc_info=True)
        return []

def extract_label_for_single(soup: BeautifulSoup)-> Optional[str]:
    """Tries to find the title from BeautifulSoup object
    returns None if not found

    Args:
        soup (BeautifulSoup): BeautifulSoup object of the webpage

    Returns:
        Optional[str]: returns Label if found else None
    """
    long_title_tag = soup.find("h3", class_="long-title")
    if long_title_tag:
        return [long_title_tag.get_text()]
    return []

def extract_ndc_codes_for_single(soup: BeautifulSoup)->list:
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

    return [related_NDCs]


def extract_packager_for_single(soup: BeautifulSoup)->list:
    packager_strong_tag = soup.find('strong', string=lambda text: text and "Packager:" in text)

    if packager_strong_tag:
        # The parent of the <strong> tag is the <li> tag
        packager_li_tag = packager_strong_tag.find_parent('li')
        if packager_li_tag:
            # Get all text from the <li> tag and strip whitespace
            li_text = packager_li_tag.get_text(strip=True)

            # Split the text by "Packager:" and take the part after it
            # Then, strip any remaining whitespace
            packager_name = li_text.split("Packager:")[1].strip()
            return [packager_name]
        else:
            return []
    else:
        return []


#@retry(stop=stop_after_attempt(10), wait=wait_exponential(multiplier=3, min=1, max=30))
def extract_drug_info(converted):

    """
    Extracts drug information URLs from DailyMed for a given converted generic name.

    Args:
        converted_generic_name (str): The generic name with '+' between words.

    Returns:
        list: A list of unique drug information URLs, or None if an error occurs.
    """
    normal_url = "https://dailymed.nlm.nih.gov/dailymed/search.cfm?labeltype=human&query="
    advance_url = "https://dailymed.nlm.nih.gov/dailymed/search.cfm?adv=1&labeltype=human&query="

    normal_final_url = normal_url + converted
    advance_final_url = advance_url + converted

    labels = []
    extracted_urls = []
    NDCs = []
    packagers = []
    search = ""


    try:
        normalResponse = requests.get(normal_final_url)
        normalResponse.raise_for_status()  # Raise an exception for bad status codes
        normalSoup = BeautifulSoup(normalResponse.content, 'html.parser')

        advanceResponse = requests.get(advance_final_url)
        advanceResponse.raise_for_status()  # Raise an exception for bad status codes
        advanceSoup = BeautifulSoup(advanceResponse.content, 'html.parser')

        normal_drug_info_link_single = normalSoup.find('a', class_='drug-info-link')
        advance_drug_info_link_single = advanceSoup.find('a', class_='drug-info-link')

        if "setid" in normalResponse.url or normal_drug_info_link_single:
            logger.info(f"Trying Normal search for '{converted}'.")
            soup = normalSoup
            response = normalResponse
            search = "Normal"
        elif "setid" in advanceResponse.url or advance_drug_info_link_single:
            logger.info(f"Normal search for '{converted}' did not yield a direct drug page. Trying advanced search.")
            soup = advanceSoup
            response = advanceResponse
            search = "Advance"
        else:
            logger.info(f"Tried both normal & advance search for '{converted}' but no results.")
            soup = normalSoup
            response = normalResponse
            search = "Tried both normal & advance"



        labels_in_one_page = extract_drug_labels(soup)
        NDCs_in_one_page = extract_ndc_codes(soup)
        packagers_in_one_page = extract_packagers(soup)
        # Check for direct single page or single page without drug-info-link
        drug_info_link_single = soup.find('a', class_='drug-info-link')
        if drug_info_link_single:
            pass
            #url_string = add_dailymed_prefix_if_not(drug_info_link_single['href'])
            #extracted_urls.append(url_string)
        elif "setid" in response.url:
            url_string = add_dailymed_prefix_if_not(response.url)
            extracted_urls.append(url_string)
            label = extract_label_for_single(soup)
            NDCs = extract_ndc_codes_for_single(soup)
            packager = extract_packager_for_single(soup)
            #print(label, extracted_urls, NDCs, packager)
            return  label, extracted_urls, NDCs, packager, search

        # Check for multiple URLs or a single page with multiple drug-info-links
        drug_info_links_multiple = soup.find_all('a', class_='drug-info-link')
        if drug_info_links_multiple:
            for i in range(len(drug_info_links_multiple)):
                labels.append(labels_in_one_page[i])
                extracted_urls.append("https://dailymed.nlm.nih.gov" + drug_info_links_multiple[i]['href'])
                NDCs.append(NDCs_in_one_page[i])
                packagers.append(packagers_in_one_page[i])

        # Handle pagination if the initial search leads to a page with 'query=' in the URL

        next_page_link = soup.find('a', class_='next-link')
        while next_page_link:
            next_page_url = "https://dailymed.nlm.nih.gov" + next_page_link['href']
            try:
                next_response = requests.get(next_page_url)
                next_response.raise_for_status()
                next_soup = BeautifulSoup(next_response.content, 'html.parser')

                labels_in_one_page = extract_drug_labels(next_soup)
                NDCs_in_one_page = extract_ndc_codes(next_soup)
                packagers_in_one_page = extract_packagers(next_soup)

                drug_info_links_on_page = next_soup.find_all('a', class_='drug-info-link')
                drug_info_links_on_page = list(drug_info_links_on_page)
                for i in range(len(drug_info_links_on_page)):
                    labels.append(labels_in_one_page[i])
                    extracted_urls.append("https://dailymed.nlm.nih.gov" + drug_info_links_on_page[i]['href'])
                    NDCs.append(NDCs_in_one_page[i])
                    packagers.append(packagers_in_one_page[i])

                next_page_link = next_soup.find('a', class_='next-link')
                time.sleep(1) # Be respectful of the website
            except requests.exceptions.RequestException as e:
                logger.error(f"Error fetching next page {next_page_url} for '{converted}': {e}", exc_info=True)
                break # Stop pagination if an error occurs
            except Exception as e:
                logger.error(f"An unexpected error occurred during pagination for '{converted}': {e}", exc_info=True)
                break

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching search results for {converted}: {e}")
        return None
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        return None

    return labels, extracted_urls, NDCs, packagers, search