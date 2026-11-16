"""
Dosage Calculator Module

This module handles parsing, normalizing, and calculating dosage information
from pharmaceutical drug labels, including frequency standardization and
yearly dosage projections.

Functions:
    parse_dose_amount: Extract numeric dose and unit
    standardize_frequency: Convert frequency descriptions to standard format
    calculate_yearly_dosage: Estimate total annual dosage
    calculate_doses_per_year: Calculate number of doses in 365 days
    parse_dosage_scenario: Extract all dosage information from text
"""

import re
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Frequency standardization mapping
FREQUENCY_PATTERNS = {
    r"(?:once|1x)?\s*daily": "once daily",
    r"twice\s*daily": "twice daily",
    r"three?\s*times\s*daily": "three times daily",
    r"(?:every|q)(?:\s*)(\d+)(?:\s*)week[s]?": "q{num}W",
    r"(?:every|q)(?:\s*)(\d+)(?:\s*)day[s]?": "q{num}D",
    r"(?:every|q)(?:\s*)(\d+)(?:\s*)hour[s]?": "q{num}H",
    r"weekly": "weekly",
    r"bi?-?weekly": "biweekly",
    r"monthly": "monthly",
    r"bi?-?monthly": "bimonthly",
}

# Unit conversion to mg (base unit)
UNIT_CONVERSIONS = {
    "mg": 1.0,
    "mcg": 0.001,  # micrograms to mg
    "µg": 0.001,
    "ug": 0.001,
    "g": 1000.0,   # grams to mg
    "iu": 0.00001,  # IU conversion depends on drug, but typical ratio
    "units": 0.001,
    "miu": 1.0,    # milli-international units
}

@dataclass
class DoseAmount:
    """Represents a dose with amount and unit."""
    value: float
    unit: str
    
    def to_mg(self) -> float:
        """Convert dose to milligrams."""
        if self.unit.lower() in UNIT_CONVERSIONS:
            return self.value * UNIT_CONVERSIONS[self.unit.lower()]
        logger.warning(f"Unknown unit: {self.unit}, returning original value")
        return self.value


def parse_dose_amount(text: str) -> Optional[DoseAmount]:
    """
    Extract numeric dose amount and unit from text.
    
    Args:
        text (str): Text containing dose information
        
    Returns:
        Optional[DoseAmount]: Parsed dose with amount and unit, or None
        
    Examples:
        >>> parse_dose_amount("200 mg IV q3w")
        DoseAmount(value=200.0, unit='mg')
        
        >>> parse_dose_amount("50 mcg once daily")
        DoseAmount(value=50.0, unit='mcg')
    """
    # Pattern: number (with optional decimal) + optional spacing + unit
    pattern = r'(\d+(?:\.\d+)?)\s*([a-z]+)'
    
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        try:
            value = float(match.group(1))
            unit = match.group(2).strip()
            return DoseAmount(value=value, unit=unit)
        except ValueError:
            logger.warning(f"Could not parse dose value from: {text}")
    
    return None


def standardize_frequency(frequency_text: str) -> Optional[str]:
    """
    Convert frequency description to standardized format.
    
    Converts natural language frequencies to standard abbreviations:
    - "once daily" → "once daily"
    - "every 3 weeks" → "q3W"
    - "twice daily" → "twice daily"
    
    Args:
        frequency_text (str): Frequency description from label
        
    Returns:
        Optional[str]: Standardized frequency, or None if not recognized
        
    Examples:
        >>> standardize_frequency("every 3 weeks")
        'q3W'
        
        >>> standardize_frequency("twice daily")
        'twice daily'
        
        >>> standardize_frequency("q2w")
        'q2W'
    """
    if not frequency_text:
        return None
    
    text = frequency_text.lower().strip()
    
    # Handle already standardized formats
    if re.match(r'^q\d+[dwh]$', text, re.IGNORECASE):
        # Convert to uppercase suffix: q2w → q2W
        return re.sub(r'(q\d+)([dwh])', lambda m: m.group(1) + m.group(2).upper(), text)
    
    # Check frequency patterns
    for pattern, replacement in FREQUENCY_PATTERNS.items():
        if "{num}" in replacement:
            # Pattern with number extraction
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                num = match.group(1)
                return replacement.format(num=num)
        else:
            # Simple pattern matching
            if re.search(pattern, text, re.IGNORECASE):
                return replacement
    
    logger.debug(f"Could not standardize frequency: {frequency_text}")
    return None


def calculate_doses_per_year(frequency: str) -> Optional[int]:
    """
    Calculate approximate number of doses in a 365-day year.
    
    Args:
        frequency (str): Standardized frequency (e.g., "q3W", "once daily")
        
    Returns:
        Optional[int]: Number of doses per year (rounded), or None
        
    Examples:
        >>> calculate_doses_per_year("once daily")
        365
        
        >>> calculate_doses_per_year("q3W")
        17
        
        >>> calculate_doses_per_year("weekly")
        52
    """
    if not frequency:
        return None
    
    freq = frequency.lower().strip()
    
    try:
        # Once daily
        if "once daily" in freq or "daily" in freq and "twice" not in freq:
            return 365
        
        # Twice daily
        if "twice daily" in freq:
            return 730
        
        # Three times daily
        if "three times daily" in freq or "thrice daily" in freq:
            return 1095
        
        # Weekly
        if "weekly" in freq and "bi" not in freq:
            return 52
        
        # Biweekly
        if "biweekly" in freq:
            return 26
        
        # Every X weeks pattern (q3W, q2w, etc.)
        match = re.search(r'q(\d+)w', freq, re.IGNORECASE)
        if match:
            interval_weeks = int(match.group(1))
            return round(52 / interval_weeks)
        
        # Every X days pattern (q3D, etc.)
        match = re.search(r'q(\d+)d', freq, re.IGNORECASE)
        if match:
            interval_days = int(match.group(1))
            return round(365 / interval_days)
        
        # Monthly
        if "monthly" in freq and "bi" not in freq:
            return 12
        
        # Bimonthly
        if "bimonthly" in freq:
            return 6
    
    except Exception as e:
        logger.warning(f"Error calculating doses per year for {frequency}: {e}")
    
    return None


def calculate_yearly_dosage(
    dose_amount: float,
    dose_unit: str,
    frequency: str,
    max_duration_weeks: Optional[int] = None,
    cycle_duration_weeks: Optional[int] = None,
    max_cycles: Optional[int] = None
) -> Optional[str]:
    """
    Calculate estimated yearly dosage based on dose, frequency, and duration.
    
    Args:
        dose_amount (float): Numeric dose value
        dose_unit (str): Unit of dose (mg, mcg, g)
        frequency (str): Standardized frequency (q3W, once daily, etc.)
        max_duration_weeks (int, optional): Total treatment duration in weeks
        cycle_duration_weeks (int, optional): Duration of one cycle in weeks
        max_cycles (int, optional): Maximum number of cycles
        
    Returns:
        Optional[str]: Formatted yearly dosage with unit (e.g., "3400 mg")
        
    Examples:
        >>> calculate_yearly_dosage(100, "mg", "once daily")
        '36500 mg'
        
        >>> calculate_yearly_dosage(200, "mg", "q3W", max_cycles=32)
        '6400 mg (32 cycles)'
    """
    if dose_amount is None or not frequency:
        return None
    
    # Convert to mg if needed
    if dose_unit.lower() in UNIT_CONVERSIONS:
        dose_mg = dose_amount * UNIT_CONVERSIONS[dose_unit.lower()]
    else:
        logger.warning(f"Unknown unit {dose_unit}, using original value")
        dose_mg = dose_amount
    
    # Calculate doses per year
    doses_per_year = calculate_doses_per_year(frequency)
    
    if doses_per_year is None:
        return None
    
    # Check if treatment is limited in duration
    if max_duration_weeks and max_duration_weeks < 52:
        # Limited duration: calculate total for the specified weeks
        yearly_dosage = dose_mg * doses_per_year * (max_duration_weeks / 52)
        return f"{yearly_dosage:.0f} mg (over {max_duration_weeks} weeks)"
    
    elif max_cycles and cycle_duration_weeks:
        # Cycle-based treatment
        total_weeks = max_cycles * cycle_duration_weeks
        yearly_dosage = dose_mg * doses_per_year * (min(total_weeks, 52) / 52)
        return f"{yearly_dosage:.0f} mg ({max_cycles} cycles)"
    
    else:
        # Continuous/annual treatment
        yearly_dosage = dose_mg * doses_per_year
        return f"{yearly_dosage:.0f} mg"


def parse_dosage_scenario(text: str) -> Dict:
    """
    Extract comprehensive dosage information from pharmaceutical text.
    
    Args:
        text (str): Dosage section from drug label
        
    Returns:
        Dict: Dictionary with parsed dosage information
            - dose_amount: Numeric dose value
            - dose_unit: Unit (mg, mcg, etc.)
            - frequency: Standardized frequency
            - route: Route of administration (IV, oral, etc.)
            - cycle_duration_weeks: Weeks per cycle (if applicable)
            - max_cycles: Maximum number of cycles
            - max_duration_weeks: Total treatment duration
            - yearly_dosage: Estimated annual dosage
            
    Example:
        >>> text = "200 mg IV once daily for 28 days, then 14-day rest"
        >>> parse_dosage_scenario(text)
        {
            'dose_amount': 200.0,
            'dose_unit': 'mg',
            'frequency': 'once daily',
            'route': 'IV',
            'cycle_duration_weeks': 6,  # 28+14 days
            'max_cycles': None,
            'max_duration_weeks': None,
            'yearly_dosage': '72800 mg'
        }
    """
    if not text:
        return {}
    
    result = {}
    
    # Extract dose amount
    dose = parse_dose_amount(text)
    if dose:
        result['dose_amount'] = dose.value
        result['dose_unit'] = dose.unit
    
    # Extract frequency
    # Look for common frequency patterns
    freq_match = re.search(
        r'(?:give|administer)?(?:\s*)([a-z\d\s]+?)(?:\s*)(?:for|through|during|over)',
        text,
        re.IGNORECASE
    )
    if not freq_match and dose:
        # Try to find frequency near the dose
        dose_pos = text.find(str(dose.value))
        if dose_pos >= 0:
            nearby_text = text[dose_pos:dose_pos + 200]
            freq_match = re.search(
                r'(?:once|twice|daily|weekly|q\d+[dwh]|every|each)',
                nearby_text,
                re.IGNORECASE
            )
    
    if freq_match:
        freq_text = freq_match.group(0) if freq_match else ""
        frequency = standardize_frequency(freq_text)
        if frequency:
            result['frequency'] = frequency
    
    # Extract route of administration
    route_match = re.search(
        r'\b(IV|oral|PO|intravenous|subcutaneous|IM|intramuscular|topical)\b',
        text,
        re.IGNORECASE
    )
    if route_match:
        result['route'] = route_match.group(1).upper()
    
    # Extract cycle duration
    cycle_match = re.search(r'(\d+)[-\s]*day(?:\s*)cycle[s]?', text, re.IGNORECASE)
    if cycle_match:
        cycle_days = int(cycle_match.group(1))
        result['cycle_duration_weeks'] = cycle_days / 7
    
    # Extract max cycles
    cycles_match = re.search(r'(?:for|up to)(?:\s*)(\d+)(?:\s*)cycle[s]?', text, re.IGNORECASE)
    if cycles_match:
        result['max_cycles'] = int(cycles_match.group(1))
    
    # Extract max duration (in weeks)
    duration_weeks = None
    weeks_match = re.search(r'(?:for|through|up to)(?:\s*)(\d+)(?:\s*)week[s]?', text, re.IGNORECASE)
    if weeks_match:
        duration_weeks = int(weeks_match.group(1))
    
    months_match = re.search(r'(\d+)(?:\s*)month[s]?', text, re.IGNORECASE)
    if months_match and not duration_weeks:
        duration_weeks = int(months_match.group(1)) * 4.3  # Approximate
    
    if duration_weeks:
        result['max_duration_weeks'] = duration_weeks
    
    # Calculate yearly dosage if we have enough information
    if 'dose_amount' in result and 'frequency' in result:
        yearly = calculate_yearly_dosage(
            result.get('dose_amount'),
            result.get('dose_unit'),
            result.get('frequency'),
            result.get('max_duration_weeks'),
            result.get('cycle_duration_weeks'),
            result.get('max_cycles')
        )
        if yearly:
            result['yearly_dosage'] = yearly
    
    return result


if __name__ == "__main__":
    # Configure logging for testing
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s:%(lineno)d - [%(levelname)s] %(message)s"
    )
    
    # Test dosage parsing
    test_cases = [
        "200 mg IV once daily for 28 days, then 14-day rest, repeat for up to 12 cycles",
        "50 mg orally once daily",
        "Variable dosing: 1-2 mg/kg IV every 3 weeks",
        "5 mg/kg intravenously over 90 minutes, followed by 2.5 mg/kg weekly starting week 2",
    ]
    
    for test in test_cases:
        print(f"\nInput: {test}")
        result = parse_dosage_scenario(test)
        print(f"Parsed: {result}")
