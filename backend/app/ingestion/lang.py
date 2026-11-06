def arabic_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    arabic = sum(1 for c in letters if "؀" <= c <= "ۿ" or "ݐ" <= c <= "ݿ")
    return arabic / len(letters)


def detect_lang(text: str) -> str:
    """'ar' or 'en' (the only languages in scope)."""
    return "ar" if arabic_ratio(text) >= 0.3 else "en"
