# -*- coding: utf-8 -*-
"""Gregorian ↔ Ethiopian calendar helpers (JDN-based)."""
from datetime import date, timedelta

# Amharic month names (index 1..13) — used in CBHI claim titles.
ETH_MONTHS_AMHARIC = {
    1: u'መስከረም',
    2: u'ጥቅምት',
    3: u'ኅዳር',
    4: u'ታኅሣሥ',
    5: u'ጥር',
    6: u'የካቲት',
    7: u'መጋቢት',
    8: u'ሚያዝያ',
    9: u'ግንቦት',
    10: u'ሰኔ',
    11: u'ሀምሌ',
    12: u'ነሐሴ',
    13: u'ጳጉሜን',
}

ETH_MONTHS_SELECTION = [
    ('1', u'Meskerem (መስከረም)'),
    ('2', u'Tikimt (ጥቅምት)'),
    ('3', u'Hidar (ኅዳር)'),
    ('4', u'Tahsas (ታኅሣሥ)'),
    ('5', u'Tir (ጥር)'),
    ('6', u'Yekatit (የካቲት)'),
    ('7', u'Megabit (መጋቢት)'),
    ('8', u'Miazia (ሚያዝያ)'),
    ('9', u'Ginbot (ግንቦት)'),
    ('10', u'Sene (ሰኔ)'),
    ('11', u'Hamle (ሀምሌ)'),
    ('12', u'Nehasse (ነሐሴ)'),
    ('13', u'Pagumen (ጳጉሜን)'),
]


def _gregorian_to_jdn(year, month, day):
    a = (14 - month) // 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    return day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045


def _jdn_to_gregorian(jdn):
    a = jdn + 32044
    b = (4 * a + 3) // 146097
    c = a - (146097 * b) // 4
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = 100 * b + d - 4800 + m // 10
    return date(year, month, day)


def gregorian_to_ethiopian(gdate):
    """Return (year, month, day) in Ethiopian calendar."""
    if not gdate:
        return None
    try:
        string_types = basestring  # noqa: F821  (Python 2)
    except NameError:
        string_types = str
    if isinstance(gdate, string_types):
        parts = gdate[:10].split('-')
        gdate = date(int(parts[0]), int(parts[1]), int(parts[2]))
    jdn = _gregorian_to_jdn(gdate.year, gdate.month, gdate.day)
    r = (jdn - 1723856) % 1461
    n = (r % 365) + 365 * (r // 1460)
    year = 4 * ((jdn - 1723856) // 1461) + (r // 365) - (r // 1460)
    month = n // 30 + 1
    day = n % 30 + 1
    return year, month, day


def ethiopian_to_gregorian(year, month, day):
    """Return Gregorian date.date for an Ethiopian Y/M/D."""
    year = int(year)
    month = int(month)
    day = int(day)
    jdn = (1723856 + 365) + 365 * (year - 1) + (year // 4) + 30 * month + day - 31
    return _jdn_to_gregorian(jdn)


def ethiopian_month_date_range(eth_year, eth_month):
    """Return (gregorian_start, gregorian_end_inclusive) for an Ethiopian month."""
    eth_year = int(eth_year)
    eth_month = int(eth_month)
    start = ethiopian_to_gregorian(eth_year, eth_month, 1)
    if eth_month == 13:
        # Pagumen: 5 days, or 6 in Ethiopian leap years (year % 4 == 3)
        last_day = 6 if (eth_year % 4 == 3) else 5
        end = ethiopian_to_gregorian(eth_year, 13, last_day)
    elif eth_month == 12:
        end = ethiopian_to_gregorian(eth_year + 1, 1, 1) - timedelta(days=1)
    else:
        end = ethiopian_to_gregorian(eth_year, eth_month + 1, 1) - timedelta(days=1)
    return start, end


def format_ethiopian_date(gdate):
    """Format as dd/mm/yyyy (Ethiopian)."""
    eth = gregorian_to_ethiopian(gdate)
    if not eth:
        return ''
    year, month, day = eth
    return '%02d/%02d/%04d' % (day, month, year)


def eth_month_amharic(month):
    return ETH_MONTHS_AMHARIC.get(int(month), u'')
