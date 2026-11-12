import re

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'[®™«»"\'~]', ' ', text)
    text = re.sub(r'\b(ооо|зао|оао|пао|ао|гмбх|лтд|ltd|gmbh|inc|llc)\b', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def normalize_dose_value(raw: str):
    """Normalize raw dose string to canonical representation (e.g. 250 мкг -> 0.25 мг)."""
    if not raw:
        return None
    raw = raw.lower().replace(' ', '').replace(',', '.')
    
    # 1. Complex concentration: 100мг/5мл -> 20мг/мл
    m = re.match(r'^(\d+(?:\.\d+)?)(мг|мкг|г)/(\d+(?:\.\d+)?)(мл)$', raw)
    if m:
        val_top = float(m.group(1))
        unit_top = m.group(2)
        val_bot = float(m.group(3))
        if unit_top == 'мкг':
            val_top /= 1000.0
        elif unit_top == 'г':
            val_top *= 1000.0
        conc = val_top / val_bot
        return f"{round(conc, 4):g}мг/мл"
        
    # 2. Simple weight: 250мкг -> 0.25мг, 0.02г -> 20мг
    m = re.match(r'^(\d+(?:\.\d+)?)(мкг|мг|г)$', raw)
    if m:
        num = float(m.group(1))
        unit = m.group(2)
        if unit == 'мкг':
            num /= 1000.0
        elif unit == 'г':
            num *= 1000.0
        return f"{round(num, 4):g}мг"
        
    return raw

def extract_dosage(text: str):
    """Extract numeric dosage and unit: e.g. '120 мг', '1.5 мг', '100 мг/5 мл', '15000 МЕ/мл'."""
    if not text:
        return None
    text = text.replace(',', '.')
    # complex: 100 мг/5 мл, 20 мкг/доза, 15000 МЕ/мл, 300 ЕД/мл
    m = re.search(r'(\d+(?:\.\d+)?\s*(?:мг|мкг|г|ед|ме|тыс\.?\s*ед|тыс\.?\s*ме)\s*/\s*\d*(?:\.\d+)?\s*(?:мл|доза))', text, re.IGNORECASE)
    if m:
        raw = m.group(1).lower().replace(' ', '')
        return normalize_dose_value(raw)
        
    # simple: 120 мг, 250 мкг, 0.25 мг, 10%
    m = re.search(r'(\d+(?:\.\d+)?\s*(?:мг/мл|мкг/доза|тыс\.?\s*ме|тыс\.?\s*ед|ме/мл|ед/мл|мг|мкг|г|ед|ме|%))', text, re.IGNORECASE)
    if m:
        raw = m.group(1).lower().replace(' ', '')
        return normalize_dose_value(raw)
    return None

def extract_pack_count(text: str):
    """Extract pack size: №100, №30, Х112, 10*3 -> 30."""
    if not text:
        return None
    # Strip registration number suffix so we don't accidentally match numbers in РУ
    text_clean = re.split(r'\b(РУ|Р N|ЛП-|ЛСР-|Рег\.:)\b', text, flags=re.IGNORECASE)[0]
    
    # 1. Match №30, в„–30, N 30, x112
    m = re.search(r'(?:№|в„–|\bn\b|\b[хx]\b)\s*(\d{1,4})', text_clean, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        if 1 <= val <= 2000:
            return val
            
    # 2. Match e.g. 100 таб, 60 капс
    m = re.search(r'(\d{1,4})\s*(?:таб|капс|амп|фл|доз|пак)', text_clean, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        if 1 <= val <= 2000:
            return val
    return None

def extract_keywords(text: str):
    """Extract first 1-2 distinctive drug name tokens."""
    if not text:
        return []
    cleaned = clean_text(text).lower()
    first_part = cleaned.split(',')[0].strip()
    words = re.findall(r'[а-яёa-z0-9\-]+', first_part)
    return [w for w in words if len(w) > 2 and not w.isdigit()]
