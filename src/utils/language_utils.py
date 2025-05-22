import langcodes
from typing import Tuple, List, Optional

# Popular languages mapping - code to display name
POPULAR_LANGUAGES = {
    'en': 'English',
    'es': 'Spanish', 
    'fr': 'French',
    'de': 'German',
    'it': 'Italian',
    'pt': 'Portuguese',
    'ru': 'Russian',
    'ja': 'Japanese',
    'ko': 'Korean',
    'zh': 'Chinese',
    'ar': 'Arabic',
    'hi': 'Hindi',
    'nl': 'Dutch',
    'sv': 'Swedish',
    'no': 'Norwegian',
    'da': 'Danish',
    'fi': 'Finnish',
    'pl': 'Polish',
    'cs': 'Czech',
    'hu': 'Hungarian',
    'tr': 'Turkish',
    'th': 'Thai',
    'vi': 'Vietnamese',
    'id': 'Indonesian',
    'ms': 'Malay',
    'tl': 'Filipino',
    'uk': 'Ukrainian',
    'bg': 'Bulgarian',
    'hr': 'Croatian',
    'sk': 'Slovak',
    'sl': 'Slovenian',
    'et': 'Estonian',
    'lv': 'Latvian',
    'lt': 'Lithuanian',
    'ro': 'Romanian',
    'el': 'Greek',
    'he': 'Hebrew',
    'fa': 'Persian',
    'ur': 'Urdu',
    'bn': 'Bengali',
    'ta': 'Tamil',
    'te': 'Telugu',
    'ml': 'Malayalam',
    'kn': 'Kannada',
    'gu': 'Gujarati',
    'pa': 'Punjabi',
    'mr': 'Marathi',
    'ne': 'Nepali',
    'si': 'Sinhala',
    'my': 'Myanmar',
    'km': 'Khmer',
    'lo': 'Lao',
    'ka': 'Georgian',
    'hy': 'Armenian',
    'az': 'Azerbaijani',
    'kk': 'Kazakh',
    'ky': 'Kyrgyz',
    'uz': 'Uzbek',
    'tg': 'Tajik',
    'mn': 'Mongolian'
}

def get_language_name(code: str) -> str:
    """Convert language code to full name using langcodes library."""
    try:
        # First check our popular languages for consistency
        if code.lower() in POPULAR_LANGUAGES:
            return POPULAR_LANGUAGES[code.lower()]
        
        # Use langcodes library for other languages
        lang = langcodes.Language.make(language=code)
        return lang.display_name()
    except:
        # Fallback to code if all else fails
        return code.upper()

def get_language_code(name_or_code: str) -> Optional[str]:
    """Convert language name to code or validate existing code."""
    input_lower = name_or_code.lower().strip()
    
    # Check if it's already a valid code in our popular languages
    if input_lower in POPULAR_LANGUAGES:
        return input_lower
    
    # Check if it's a language name in our popular languages
    for code, name in POPULAR_LANGUAGES.items():
        if name.lower() == input_lower:
            return code
    
    # Try to use langcodes to find the language
    try:
        # Try to match by name
        lang = langcodes.find(input_lower)
        if lang and len(lang.language) >= 2:
            return lang.language
    except:
        pass
    
    # Try to validate as a code
    try:
        lang = langcodes.Language.make(language=input_lower)
        if lang and len(lang.language) >= 2:
            return lang.language
    except:
        pass
    
    return None

def is_valid_language(identifier: str) -> bool:
    """Check if identifier is a valid language code or name."""
    return get_language_code(identifier) is not None

def normalize_language(identifier: str) -> Tuple[str, str]:
    """
    Normalize language identifier to (code, name) tuple.
    Returns None if language is not valid.
    """
    code = get_language_code(identifier)
    if code:
        name = get_language_name(code)
        return code, name
    return None, None

def get_popular_languages() -> List[Tuple[str, str]]:
    """Get list of popular languages as (code, name) tuples."""
    return [(code, name) for code, name in POPULAR_LANGUAGES.items()]

def get_language_suggestions(partial: str, limit: int = 10) -> List[Tuple[str, str]]:
    """Get language suggestions based on partial input."""
    partial_lower = partial.lower()
    suggestions = []
    
    # First, check popular languages
    for code, name in POPULAR_LANGUAGES.items():
        if (partial_lower in name.lower() or 
            partial_lower in code.lower()):
            suggestions.append((code, name))
    
    # Limit results
    return suggestions[:limit]