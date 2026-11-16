"""
NDC (National Drug Code) Standardization Module

This module provides a class to convert NDC strings from various formats
into a standardized format: XXXXX-XXXX-XX (5-4-2 or variable).

Classes:
    NDC: A class to represent and validate National Drug Codes
"""

from typing import Optional


class NDC:
    """
    Represents a standardized National Drug Code (NDC).
    
    Supports conversion from multiple formats (with/without hyphens)
    and validates the structure according to FDA guidelines.
    
    NDC Format: XXXXX-XXXX-XX (Labeler-Product-Package)
    - Labeler: 4-5 digits
    - Product: 3-4 digits
    - Package: 1-2 digits
    
    Examples:
        >>> ndc1 = NDC("70255002503")  # 11 digits, no hyphens
        >>> str(ndc1)
        '70255-0025-03'
        
        >>> ndc2 = NDC("7025-502-5", 4, 3, 2)  # 10 digits with hints
        >>> ndc2.ndc
        '07025-0502-05'
    """

    def __init__(
        self,
        ndc: str,
        len_labeler: int = None,
        len_product: int = None,
        len_package: int = None,
    ) -> None:
        """
        Initialize an NDC object.

        Args:
            ndc (str): NDC string with or without hyphens
            len_labeler (int, optional): Length of labeler code (4-5)
            len_product (int, optional): Length of product code (3-4)
            len_package (int, optional): Length of package code (1-2)

        Raises:
            AssertionError: If hyphen count is not 0 or 2
            AssertionError: If NDC without hyphens is not 8-11 digits
            AssertionError: If component lengths are invalid
            ValueError: If length parameters are logically inconsistent

        Note:
            If NDC has hyphens, other parameters are ignored.
            If NDC has no hyphens and no length parameters provided,
            a default format is assumed based on total length.
        """
        # Remove internal spaces
        ndc = ndc.replace(" ", "")
        cnt_hyphens = ndc.count("-")

        # Validate hyphen count
        if cnt_hyphens and cnt_hyphens != 2:
            raise AssertionError("NDC must contain either 0 or 2 hyphens")
        
        if not cnt_hyphens and not (8 <= len(ndc) <= 11):
            raise AssertionError("NDC without hyphens must be 8-11 digits long")

        # Parse hyphenated format
        if cnt_hyphens:
            labeler, product, package = ndc.split("-")
            assert 4 <= len(labeler) <= 5, "Labeler code must be 4-5 digits"
            assert 3 <= len(product) <= 4, "Product code must be 3-4 digits"
            assert 1 <= len(package) <= 2, "Package code must be 1-2 digits"

        else:
            # Handle non-hyphenated format with optional length hints
            lbl_none = len_labeler is None
            pro_none = len_product is None
            pkg_none = len_package is None
            all_none = lbl_none and pro_none and pkg_none

            # Validate parameter logic
            unacceptable_flag = (
                (lbl_none and pro_none and not pkg_none)
                or (lbl_none and not pro_none and pkg_none)
                or (not lbl_none and pro_none and pkg_none)
            )
            if unacceptable_flag:
                raise ValueError(
                    "Either provide all lengths as None, or at least 2 non-None lengths"
                )

            # Infer missing lengths from provided lengths
            if lbl_none and not pro_none and not pkg_none:
                len_labeler = len(ndc) - len_product - len_package
            elif not lbl_none and pro_none and not pkg_none:
                len_product = len(ndc) - len_labeler - len_package
            elif not lbl_none and not pro_none and pkg_none:
                len_package = len(ndc) - len_labeler - len_product

            # Validate inferred/provided lengths
            if len_labeler:
                assert 4 <= len_labeler <= 5, "Labeler must be 4-5 digits"
            if len_product:
                assert 3 <= len_product <= 4, "Product must be 3-4 digits"
            if len_package:
                assert 1 <= len_package <= 2, "Package must be 1-2 digits"

            # Use defaults if all lengths are None
            if all_none:
                if len(ndc) == 11:
                    len_labeler, len_product, len_package = 5, 4, 2
                elif len(ndc) == 10:
                    len_labeler, len_product, len_package = 4, 4, 2
                elif len(ndc) == 9:
                    len_labeler, len_product, len_package = 4, 3, 2
                elif len(ndc) == 8:
                    len_labeler, len_product, len_package = 4, 3, 1

            # Final validation
            assert (
                len(ndc) == len_labeler + len_product + len_package
            ), f"Length mismatch: {len(ndc)} != {len_labeler} + {len_product} + {len_package}"

            # Extract components
            labeler = ndc[0:len_labeler]
            product = ndc[len_labeler : len_labeler + len_product]
            package = ndc[len_labeler + len_product :]

        # Standardize with leading zeros
        self._labeler = "0" * (5 - len(labeler)) + labeler
        self._product = "0" * (4 - len(product)) + product
        self._package = "0" * (2 - len(package)) + package
        self._ndc = f"{self.labeler}-{self.product}-{self.package}"

    @property
    def labeler(self) -> str:
        """Return the standardized labeler code (5 digits)."""
        return self._labeler

    @property
    def product(self) -> str:
        """Return the standardized product code (4 digits)."""
        return self._product

    @property
    def package(self) -> str:
        """Return the standardized package code (2 digits)."""
        return self._package

    @property
    def ndc(self) -> str:
        """Return the standardized NDC (XXXXX-XXXX-XX format)."""
        return self._ndc

    def __eq__(self, other) -> bool:
        """Check equality based on standardized NDC format."""
        if not isinstance(other, NDC):
            return False
        return self.ndc == other.ndc

    def __hash__(self) -> int:
        """Return hash of standardized NDC for use in sets/dicts."""
        return hash(self._ndc)

    def __repr__(self) -> str:
        """Return string representation of NDC."""
        return self.ndc

    def __str__(self) -> str:
        """Return string representation of NDC."""
        return self.ndc


if __name__ == "__main__":
    # Example usage and testing
    ndc1 = NDC("70255002503")
    ndc2 = NDC("7025502503", len_labeler=5, len_package=2)
    
    print(f"NDC 1: {ndc1}")
    print(f"  Labeler: {ndc1.labeler}, Product: {ndc1.product}, Package: {ndc1.package}")
    
    print(f"\nNDC 2: {ndc2}")
    print(f"  Equal: {ndc1 == ndc2}")
