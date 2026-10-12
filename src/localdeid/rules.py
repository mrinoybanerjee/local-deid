"""Rules stage: both language packs on all text (SPEC 4.3, spanish_deid.md section 3).

Format rules find dates, times, phones, emails, URLs, addresses, ZIP codes,
and record numbers. Context triggers find names and places next to a word
that marks them: honorifics, kin words, school and hospital words, "born at",
"seen by", "works at", "vive en". A trigger can give a role (role_rank 1).
"""

from __future__ import annotations

import re
from typing import Iterator

from .generic import all_generic
from .spans import Candidate

U = r"A-ZÁÉÍÓÚÑÜÀÈÌÒÙÂÊÎÔÛÇ"  # uppercase letters used in EN/ES names
L = r"a-záéíóúñüàèìòùâêîôûç"
_NOT_TITLE = r"(?!(?:Dr|Dra|Mr|Mrs|Ms|Mx|Miss|Sr|Sra|Srta|Prof|Profa|Lic|Ing)\b)"
NAME_TOKEN = rf"{_NOT_TITLE}[{U}][{L}{U}]*(?:['’][{U}][{L}]+)?(?:-[{U}{L}][{L}{U}]*)?"
PARTICLE = (r"(?:viuda[ \t]+de|vda\.?[ \t]+de|de[ \t]+la|de[ \t]+los|de[ \t]+las|del|de|da|di|van[ \t]+der|van|"
            r"von|y|la|le|st\.?|mc|o')")
# One to four capitalized tokens, with surname particles allowed between them.
_H = r"[ \t]+"  # horizontal space only: a name never runs across a line break
# "Hannah R. Soto", "Mateo A.": a single middle or trailing initial is part of the name.
_INITIAL = rf"[{U}]\.(?![\w])"
NAME = rf"{NAME_TOKEN}(?:{_H}(?:{PARTICLE}{_H})?(?:{NAME_TOKEN}|{_INITIAL})){{0,3}}"

# Capitalized words that follow a trigger but are not names.
NOT_NAMES = frozenset("""
the a an and or but he she they his her their it this that these those i we you
reported said stated noted shared explained mentioned asked agreed indicated
el la los las un una y o pero él ella ellos su sus este esta ese esa yo
dijo comentó reportó mencionó explicó indicó compartió preguntó
mom dad mother father mamá papá mami papi madre padre
""".split())

# "may" in lowercase is the verb, never the month, even under IGNORECASE.
MONTHS_EN = (r"(?-i:(?!may\b))(?:January|February|March|April|May|June|July|August|September|October|"
             r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)")
MONTHS_ES = (r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|"
             r"noviembre|diciembre|ene|feb|mar|abr|may|jun|jul|ago|sept|sep|oct|nov|dic")
WEEKDAYS = (r"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Tues|Wed|Thu|"
            r"Thurs|Fri|Sat|Sun|lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|"
            r"domingo")
ORD = r"(?:st|nd|rd|th|ro|do|er|º|°)"
DAY = rf"(?:[12]\d|3[01]|0?[1-9]){ORD}?"
YEAR = r"(?:19|20)\d{2}"

_I = re.IGNORECASE


def _c(p: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(p, flags)


# ----------------------------------------------------------------------------- formats

DATE_PATTERNS = [
    # 03/14/2021, 3-14-21, 14.03.2021, 2021-03-14
    _c(r"(?<![\w/.-])(?:\d{1,2}[/.-]\d{1,2}[/.-](?:\d{4}|\d{2}))(?![\w/-]|\.\d)"),
    _c(r"(?<![\w/-])(?:19|20)\d{2}-\d{1,2}-\d{1,2}(?![\w/-])"),
    # bare month/day, when both orders could be a real date ("on 3/14", "el 14/3")
    _c(r"(?<![\w/.-])(?:0?[1-9]|1[0-2])/(?:[12]\d|3[01]|0?[1-9])(?![\w/-]|\.\d)"),
    _c(r"(?<![\w/.-])(?:1[3-9]|2\d|3[01])/(?:0?[1-9]|1[0-2])(?![\w/-]|\.\d)"),
    # March 3, March 3rd, March 3, 2026, Mar. 3 2026, Monday, March 3
    _c(rf"\b(?:(?:{WEEKDAYS}),?\s+)?(?:{MONTHS_EN})\.?\s+{DAY}(?:,?\s+{YEAR})?\b(?!\s*(?:%|/))", _I),
    # 3 March 2026, 3rd of March, the 3rd of March, 2026
    _c(rf"\b{DAY}\s+(?:of\s+)?(?:{MONTHS_EN})\.?(?:,?\s+{YEAR})?\b", _I),
    # March 2026 (month + year), Spanish "marzo de 2026" / "marzo 2026"
    _c(rf"\b(?:{MONTHS_EN})\.?,?\s+{YEAR}\b", _I),
    # Spanish: 3 de marzo (de 2026), el 1ro de marzo, primero de marzo, martes 3 de marzo
    _c(rf"\b(?:(?:{WEEKDAYS}),?\s+)?(?:{DAY}|primero)\s+de\s+(?:{MONTHS_ES})\.?(?:\s+(?:de|del)\s+{YEAR})?\b", _I),
    # Spanish US order: marzo 3, marzo 3, 2026
    _c(rf"\b(?:{MONTHS_ES})\.?\s+{DAY}(?:,?\s+{YEAR})?\b(?!\s*(?:%|/|de\b))", _I),
    _c(rf"\b(?:{MONTHS_ES})\s+(?:de\s+|del\s+)?{YEAR}\b", _I),
]

# The month abbreviation "May"/"mar"/"may" is also a word; require a capital
# or a digit right after it (handled inside the patterns by requiring DAY or YEAR).

TIME_PATTERNS = [
    # 9:30, 9:30 AM, 10:15-11:00, 9:30–10:15 a.m.
    _c(r"(?<![\w:.])(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?:\s*(?:[ap]\.?\s?m\.?|hrs?|horas?)(?![\w]))?"
       r"(?:\s*[-–—]\s*(?:[01]?\d|2[0-3]):[0-5]\d(?:\s*(?:[ap]\.?\s?m\.?)(?![\w]))?)?", _I),
    # 9:15 de la mañana, 3 de la tarde
    _c(r"(?<![\w:.])(?:[01]?\d|2[0-3])(?::[0-5]\d)?[ \t]+de[ \t]+la[ \t]+(?:mañana|tarde|noche|madrugada)\b", _I),
    # 9 AM, 3pm, 3 p.m.
    _c(r"(?<![\w:.])(?:1[0-2]|0?[1-9])\s*(?:[ap]\.\s?m\.|[ap]m)(?!\w)", _I),
    # Spanish "a las 3", "a la 1"
    _c(r"(?<=\ba las )(?:1[0-2]|0?[1-9])(?:\s*(?:y\s+media|y\s+cuarto))?\b|(?<=\ba la )1\b", _I),
]

PHONE_PATTERNS = [
    # US: (919) 555-0142, 919-555-0142, 919.555.0142, +1 919 555 0142
    _c(r"(?<![\w+])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?!\d)"),
    _c(r"(?<![\w+])\d{10}(?!\d)"),
    # Mexico +52 (1) 55 1234 5678; Central America +502/+503/+504 1234 5678
    _c(r"\+52\s?(?:1\s?)?\d{2,3}[\s.-]?\d{3,4}[\s.-]?\d{4}(?!\d)"),
    _c(r"\+50[2-4]\s?\d{4}[\s.-]?\d{4}(?!\d)"),
]
_PHONE_WORD_7 = _c(
    r"\b(?:phone|tel|cell|cel|celular|tel[eé]fono|n[uú]mero|whatsapp|text|call|llamar|marcar)\b"
    r"[\s.:#]{1,4}(?:(?:al|is|es|at)\s+)?(\d{3}[\s.-]\d{4})(?!\d)", _I)

EMAIL = _c(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w-])")
DOMAIN = _c(r"(?<![\w@./-])(?:[a-z0-9-]+\.)+(?:com|org|net|edu|gov|us|io|co|info|biz)(?:/[^\s<>()\"']*)?"
            r"(?![\w@-])", _I)
URL = _c(r"(?<![\w@])(?:https?://|www\.)[^\s<>()\"']+[^\s<>()\"'.,;:!?]", _I)

STREET_TYPES = (r"Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Way|Court|Ct|Place|Pl|"
                r"Parkway|Pkwy|Circle|Cir|Trail|Trl|Highway|Hwy|Terrace|Ter|Loop|Run|Pike|Square|Sq|"
                r"Crossing|Xing|Ridge|Point|Pt")
STREET_TYPES_ES = r"Calle|Avenida|Av|Calzada|Callej[oó]n|Privada|Carretera|Camino|Andador|Paseo"
STATES = (r"AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|"
          r"NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC|North Carolina|"
          r"South Carolina|Virginia")
ADDRESS_PATTERNS = [
    # 1234 N. Oak Ridge Dr., Apt 5B
    _c(rf"\b\d{{1,6}}[ \t]+(?:[NSEW]\.?[ \t]+)?(?:[{U}0-9][\w'.-]*[ \t]+){{1,4}}(?:{STREET_TYPES})\b\.?"
       r"(?:,?[ \t]*(?:Apt|Apartment|Unit|Suite|Ste|Lot|#)\.?[ \t]*#?[ \t]*[\w-]+)?"),
    # Cary, NC 27513 / Cary, North Carolina / Durham NC
    _c(rf"\b[{U}][{L}]+(?:[ \t]+[{U}][{L}]+){{0,2}}(?:,[ \t]+(?:{STATES})|,?[ \t]+(?:North Carolina|"
       rf"South Carolina|Virginia))\b(?![ \t]*[:#])(?:[ \t]+\d{{5}}(?:-\d{{4}})?)?"),
    # Spanish: Calle Morelos #45, Avenida Juárez 123, Col. Centro, C.P. 68000
    _c(rf"(?:\b\d{{1,6}}[ \t]+)?\b(?:Calle|Avenida|Av\.|Calzada|Callejón|Privada|Carretera|Camino|Andador|Boulevard|Blvd\.)"
       rf"[ \t]+(?:[{U}0-9][\w'.-]*[ \t]*){{1,4}}(?:[ \t]*(?:#|No\.|Núm\.|num\.)[ \t]*\d+[A-Za-z]?)?"
       rf"(?:,?[ \t]*(?:Apt|Apto|Apartment|Depto|Dpto|Int|Interior|Unit|Suite|#)\.?[ \t]*#?[ \t]*[\w-]+)?"),
    _c(rf"\b(?:Colonia|Col\.|Fraccionamiento|Fracc\.|Ejido|Rancho|Barrio|Municipio[ \t]+de)[ \t]+(?:[{U}][\w'.-]*[ \t]*){{1,4}}"),
    _c(r"\b(?:C\.\s?P\.|código postal)[ \t]*:?[ \t]*\d{5}\b", _I),
    # P.O. Box 123
    _c(r"\bP\.?\s?O\.?[ \t]+Box[ \t]+\d+\b", _I),
]
ZIP_AFTER_STATE = _c(rf"\b(?:{STATES})\s(?P<v>\d{{5}}(?:-\d{{4}})?)\b")
ZIP_LABEL = _c(r"\b(?:ZIP|zip code|Zip)\s*:?\s*(\d{5}(?:-\d{4})?)\b")

ID_LABELS = (r"Medicaid|MA|Medicare|insurance|member|policy|subscriber|group|MRN|medical record|record|"
             r"chart|account|acct|claim|authorization|auth|prior auth|PA|case|NPI|ITP|child ID|"
             r"IFSP|SSN|social security|ID|expediente|caso|póliza|poliza|seguro|número de|no\.")
ID_PATTERNS = [
    # NC Medicaid ID: 9 digits + letter
    ("MEDICAID_ID", _c(r"(?<![\w-])\d{9}[A-Z](?![\w-])")),
    # SSN
    ("ID", _c(r"(?<![\w-])\d{3}-\d{2}-\d{4}(?![\w-])")),
    # long digit runs (6+) that no format rule claimed
    ("ID", _c(r"(?<![\w.,/-])\d{6,}(?![\w,/]|\.\d)")),
    # prefixed record numbers: ITP-0038421, NC-SLP-04521, SLP-602718, AUTH-2026-00113
    ("ID", _c(r"(?<![\w-])[A-Z]{2,6}(?:-[A-Z]{2,6})*-\d{3,}(?:-\d+)*[A-Z]?(?![\w-])")),
]
_ID_AFTER_LABEL = _c(
    rf"\b(?:{ID_LABELS})\b\.?\s*(?:#|no\.|num\.|number|número|ID)?\s*[:#]?\s*"
    r"(?P<v>(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{3,}[A-Z0-9])\b", _I)
_MEDICAID_LABEL = re.compile(r"medicaid|\bMA\b", _I)

# ----------------------------------------------------------------------------- triggers

KIN = {
    "PARENT": r"mom|mommy|mother|dad|daddy|father|stepmom|stepmother|stepdad|stepfather|guardian|"
              r"mamá|mama|mami|madre|papá|papa|papi|padre|madrastra|padrastro|tutor|tutora",
    "SIBLING": r"sister|brother|sis|stepsister|stepbrother|half-sister|half-brother|twin|"
               r"hermana|hermano|hermanita|hermanito|gemela|gemelo",
    "FAMILY": r"aunt|auntie|uncle|grandma|grandmother|grandpa|grandfather|granny|nana|cousin|"
              r"godmother|godfather|niece|nephew|tía|tia|tío|tio|abuela|abuelita|abuelo|abuelito|"
              r"prima|primo|madrina|padrino|sobrina|sobrino|comadre|compadre|nanny|niñera|"
              r"babysitter|caregiver|cuidadora",
    "PEER": r"friend|classmate|peer|buddy|amigo|amiga|amiguito|amiguita|compañero|compañera|"
            r"compañerito|compañerita",
    "PET": r"dog|puppy|cat|kitten|pet|bunny|hamster|perro|perrito|perrita|gato|gatito|gatita|"
           r"mascota|conejo",
}
POSSESSIVE = r"(?:his|her|their|the child's|child's|my|your|su|sus|mi|tu|el|la|los|las)"
# su mamá Lucía | his mother, Lucía Rodríguez, | tía Carmen | her dog Max | mom (Lucía)
_KIN_RES = [
    (role, _c(rf"\b(?i:(?:{POSSESSIVE}{_H})?(?:{words})(?:{_H}de{_H}la{_H}familia|{_H}de{_H}la{_H}casa|{_H}of{_H}the{_H}family)?)"
              rf"(?:,|{_H}(?i:named|llamad[oa])|{_H}\()?{_H}[\"“'‘]?(?P<n>{NAME})"))
    for role, words in KIN.items()
]
HONORIFIC_ROLE = [
    ("PROVIDER", r"Dr|Dra|Doctor|Doctora"),
    ("TEACHER", r"maestra|maestro|teacher|profesora|profesor|Profa|Prof|Mtra|Mtro"),
    ("PERSON", r"Mr|Mrs|Ms|Mx|Miss|Mister|Sr|Sra|Srta|señora|señor|señorita|doña|don|Lic|Ing"),
]
# "Canela, the family's dog", "Max, su perrito" (a name, then what it is)
_PET_APPOSITIVE = _c(rf"(?P<n>{NAME}),{_H}(?i:(?:the{_H}|our{_H}|their{_H}|his{_H}|her{_H}|su{_H}|la{_H}|el{_H})?"
                     rf"(?:family's{_H}|family{_H})?(?:dog|puppy|cat|kitten|pet|perro|perrito|perrita|gato|gatito|"
                     rf"gatita|mascota))\b")
# "(apodo: "Santi")", "goes by Teo", "le dicen Chuy", "a.k.a. Majo"
_NICKNAME = _c(rf"(?i:\b(?:apodo|sobrenombre|nickname|a\.k\.a\.|aka|goes by|le dicen|le llaman|known as|"
               rf"called))[ \t]*:?[ \t]*[\"“'‘]?(?P<n>{NAME_TOKEN})")
_HON_RES = [
    (role, _c(rf"(?<![\w.])(?i:{words})\.?{_H}(?P<n>{NAME})")) for role, words in HONORIFIC_ROLE
]
# "Ms. Lopez's class", "en el salón de la maestra"
_CLASS_OF = _c(rf"(?P<n>{NAME_TOKEN})['’]s\s+(?:class|classroom|room)\b")

ORG_SUFFIX_DAYCARE = (r"Family Child Care Home|Child Care Home|Childcare Home|Day Care Home|Daycare Home|"
                      r"Early Head Start Center|Head Start Center|Daycare Center|Day Care Center|Child Care Center|Childcare Center|Preschool Center|"
                      r"Development Center|Daycare|Day Care|Child Care|Childcare|Child Development Center|Learning Center|"
                      r"Learning Academy|Early Learning|Preschool|Pre-School|Pre-K|Montessori|Nursery|"
                      r"Kids Academy|Head Start")
ORG_SUFFIX_SCHOOL = (r"Elementary School|Elementary|Middle School|High School|Primary School|School|"
                     r"Academy|Charter|Magnet|Christian School|Day School|Kindergarten")
ORG_SUFFIX_HOSPITAL = (r"Surgery Center|Developmental Center|Health Center|Rehabilitation Center|Children's Center|"
                       r"Children's Clinic|Hospital|Hospitals|Medical Center|Medical Group|Health System|Health Care|"
                       r"Healthcare|Children's Hospital|Clinic|Pediatrics|Pediatric Associates|"
                       r"Family Medicine|Women's Center|Birthing Center|Health")
_HON_TOKEN = r"(?:Dr|Dra|Mr|Mrs|Ms|Mx|Miss|Sr|Sra|Srta|Prof|Profa)\.?\s"
# One to five capitalized tokens on the same line (no newlines, no leading digits).
# A name never starts with a preposition, article, or determiner ("At Little Sprouts ...").
_NOT_START = (r"(?!(?:At|In|On|From|To|For|With|By|Of|The|A|An|And|Or|En|Con|Por|Para|Desde|Hasta|El|La|Los|"
              r"Las|Un|Una|Our|Your|This|That|These|Their|His|Her|Su|Sus|Each|Every|Any|Both|Mom|Dad|Mamá|"
              r"Papá)\b)")
# Spaces only between words: a tab separates table cells ("School\tDaycare" is two headers).
ORG_NAME = rf"(?:(?!{_HON_TOKEN}){_NOT_START}[{U}][\w'’&-]*[ ]+(?:(?:of|the|&|and|y|de|la|los|las|del)[ ]+)?){{1,5}}"
ORG_SUFFIX_COMPANY = (r"Partners|Associates|Group|Services|Service|Solutions|Rehab|Rehabilitation|Therapies|"
                      r"Therapy Services|Therapy Center|Therapy Group|Industries|Company|Corporation|Works|"
                      r"Enterprises|Holdings|Foods|Manufacturing|Logistics|Farms|Farm|Suites|Inn|Hotel|Motel|"
                      r"Construction|Builders|Supply|Living|Lines|Processing|Foundry|Market|Supermarket|Store|"
                      r"Mart|Bank|Motors|Automotive|Landscaping|Cleaning|Plumbing|Electric|Roofing|Trucking|"
                      r"Transport|Dairy|Bakery|Cafe|Café|Grill|Restaurant|Kitchen|Mills|Textiles|Furniture|"
                      r"Poultry|Freight|Distribution|Warehouse|Staffing|Care Center|Senior Living|Nursing Home|"
                      r"Management|Express|Processors|Therapy|Pediatric Therapy|Logística|Transporte|Construcción|"
                      r"Inn Express|Primary Care|Pediatrics|Audiology|Campus")
LEGAL_SUFFIX = (r"(?:,?[ \t]+(?:PLLC|LLC|L\.L\.C\.|Incorporated|Incorporada|Incorporado|Inc\.?|P\.C\.|PA|P\.A\.|"
                r"Corp\.?|Co\.|S\.A\.)(?![\w]))")
_CO_TOKEN = rf"(?!{_HON_TOKEN}){_NOT_START}[{U}][\w'’&.-]*"
_CO_JOIN = r"[ \t]+(?:(?:&|and|y|de|del|of|the)[ \t]+)?"
_ORG_TAIL = rf"(?:[ \t]+(?:of|de|del)[ \t]+(?:the[ \t]+)?{_CO_TOKEN}(?:[ \t]+{_CO_TOKEN}){{0,2}})?"
# "Target" the store, only in a workplace context: "works at (a) Target", "en Target",
# "Workplace: Target", "a Target store". Never "Target: /s/" or "Target words".
_TARGET_CLINICAL_AFTER = (r"[ \t]+(?i:words?|sounds?|behaviou?rs?|phonemes?|structures?|goals?|areas?|skills?|"
                          r"vocabulary|items?|palabras?|sonidos?|fonemas?|objetivos?|level|nivel)\b")
_TARGET_NOTATION = r"[ \t]*(?::[ \t]*)?(?:[/\[]|\([ \t]*[/\[])"  # "Target /s/", "Target: [k]", "Target (/s/)"
_TARGET_STORE = re.compile(
    # Employment evidence: "Workplace: Target", "cashier at Target: night shift", "took a position
    # with Target", "trabaja para Target". Only phoneme or IPA notation after it is clinical.
    r"(?:(?i:\b(?:workplace|employer|empleador[a]?|lugar[ \t]+de[ \t]+trabajo)[ \t]*:[ \t]*"
    r"|\b(?:job|jobs|employed|employee|works?|worked|working|cashier|stocker|manager|guard|clerk|"
    r"(?:a|the|new|his|her)[ \t]+position|trabaja|trabajaba|trabajo|empleo|puesto|cajer[oa]|guardia)\b"
    r"(?:[ \t]+[\w'’]+){0,3}?[ \t]+(?:with|con|at|for|en|para|in)[ \t]+(?:(?:a|the|un|una|el|la)[ \t]+)?"
    # a parent or pronoun subject: "Father drives for Target", "Papá conduce para Target"
    r"|\b(?:father|mother|dad|daddy|mom|mommy|parent|parents|stepfather|stepmother|guardian|grandmother|"
    r"grandfather|grandma|grandpa|aunt|uncle|caregiver|he|she|papá|papa|mamá|mama|padre|madre|abuela|abuelo|"
    r"tía|tia|tío|tio|él|ella)[ \t]+(?:(?:also|now|still|currently|ahora|también|tambien|todavía|todavia)[ \t]+)?"
    r"(?:works|worked|drives|drove|delivers|delivered|cleans|cleaned|cooks|cooked|stocks|manages|managed|"
    r"supervises|sells|is[ \t]+employed|was[ \t]+employed|trabaja|trabajaba|conduce|maneja|limpia|cocina|"
    r"reparte|vende)(?:[ \t]+[\w'’-]+){0,4}?"  # objects, modifiers: "delivers groceries", "los fines de semana"
    r"[ \t]+(?:with|con|at|for|en|para)[ \t]+(?:(?:a|the|un|una|el|la)[ \t]+)?))"
    rf"(?P<w>Target)\b(?!{_TARGET_CLINICAL_AFTER}|{_TARGET_NOTATION})"
    # A location preposition alone ("at Target", "en Target"): clinical words and notes after it stay text.
    r"|(?i:\b(?:at|en|from)[ \t]+(?:(?:a|the|un|una|el|la)[ \t]+)?)"
    rf"(?P<n>Target)\b(?!{_TARGET_CLINICAL_AFTER}|{_TARGET_NOTATION}|[ \t]*[:(])"
    r"|\b(?P<s>Target)(?=[ \t]+(?i:store|tienda|warehouse|distribution center|corporation)\b)")

_COMPANY_RES = [
    # "Compass Logistics", "Blue Ridge Freight Lines", "Pediatric Therapy Partners of NC"
    _c(rf"\b{_CO_TOKEN}(?:{_CO_JOIN}{_CO_TOKEN}){{0,4}}{_CO_JOIN}(?:{ORG_SUFFIX_COMPANY})\b{_ORG_TAIL}{LEGAL_SUFFIX}?"),
    # Spanish order: "Grupo de Construcción Triángulo", "Procesadora de Aves Valle Verde", "Terapias Pediátricas Carolina"
    _c(rf"\b(?:Planta|Construcción|Construcciones|Fábrica|Granja|Rancho|"
       rf"Grupo|Procesadora|Constructora|Distribuidora|Transportes|Empresa|Compañía|Restaurante|Tienda|"
       rf"Mercado|Panadería|Taller|Terapias|Terapia|Clínica|Clinica|Pediatría|Pediatria|Especialistas)"
       rf"(?:[ \t]+(?:de|del|y)[ \t]+[{L}{U}][\w'’-]*|[ \t]+[{U}][\w'’-]*){{1,6}}"),
    # "Delgado Construcción y Diseño", "Construcción Vargas y Asociados"
    _c(rf"\b{_CO_TOKEN}(?:{_CO_JOIN}{_CO_TOKEN}){{0,3}}[ \t]+y[ \t]+(?:Asociados|Diseño|Hermanos|Hijos|Compañía|Cía\.?)\b"),
    # camel-case brands: "TherapyWorks of the Piedmont", "KidsFirst Pediatrics"
    _c(rf"\b[{U}][{L}]{{2,}}[{U}][{L}]{{2,}}(?:[ \t]+[{U}][\w'’-]*)*{_ORG_TAIL}"),
    # common employers and stores named as workplaces
    _c(r"\b(?:Food Lion|Harris Teeter|Walmart|Wal-Mart|Lowe's|Home Depot|McDonald's|Chick-fil-A|"
       r"Bojangles|Amazon|Costco|Publix|Aldi|Dollar General|Smithfield|Tyson|Perdue|Mountaire|Butterball|"
       r"Cracker Barrel|Waffle House|Bank of America|Wells Fargo|SAS|Cisco|IBM|Lenovo)\b"),
    # "Target" is also the SLP word ("Target: /s/", "Target words"): see _TARGET_STORE.
    # "Carolina Bilingual Speech & Language Therapy, PLLC", "Terapias Pediátricas Bilingües de Carolina, PLLC"
    _c(rf"\b{_CO_TOKEN}(?:{_CO_JOIN}{_CO_TOKEN}){{0,6}}{LEGAL_SUFFIX}"),
]
# Disciplines, approaches, and service types: "referred to Physical Therapy", "through Total Communication".
_CLINICAL_PHRASE_WORDS = frozenset("""
physical occupational speech feeding developmental behavioral behavior applied analysis total communication
sign language signing augmentative alternative aac audiology audiological hearing vision nutrition
nutritional psychology psychological counseling social work nursing medical dental mental oral public
child care early intervention play group services evaluation therapy therapies treatment
fisioterapia terapia ocupacional física fisica lenguaje habla audiología audiologia nutrición nutricion
psicología psicologia comunicación comunicacion total intervención intervencion temprana servicios
""".split())
_GENERIC_ORG_WORDS = frozenset("""
speech language therapy therapies occupational physical early intervention developmental special
education related family support services service child children pediatric pediatrics health
behavioral feeding hearing audiology rehab rehabilitation group partners associates solutions
center programs program community social works company the of and for llc pllc inc pa
pathology department departamento office oficina unit unidad team equipo staff room sala classroom salon
ent gi nicu picu ucin individualized individualizado date fecha
salón area área division división clinic clinica clínica
""".split())
# NC health systems: a brand plus following capitalized words ("WakeMed Raleigh Campus",
# "Duke Children's Primary Care", "Cone Health Pediatrics at Wendover").
_HEALTH_BRANDS = (r"WakeMed|Duke|UNC|Atrium|Novant|Cone Health|ECU Health|Mission|Cape Fear Valley|Rex|"
                  r"Levine Children's|Brenner Children's|Vidant|CaroMont|FirstHealth|Moses Cone|Wake Forest Baptist")
_HEALTH = _c(rf"\b(?:{_HEALTH_BRANDS})(?:[ \t]+(?:Health|Children's|Raleigh|Cary|Durham|Garner|Apex))*"
             rf"(?:[ \t]+[{U}][\w'’-]*){{0,3}}(?:[ \t]+(?:at|en)[ \t]+{NAME_TOKEN})?")
_ORG_RES = [
    ("DAYCARE", _c(rf"\b{ORG_NAME}(?:{ORG_SUFFIX_DAYCARE})\b")),
    ("SCHOOL", _c(rf"\b{ORG_NAME}(?:{ORG_SUFFIX_SCHOOL})\b")),
    ("HOSPITAL", _c(rf"\b{ORG_NAME}(?:{ORG_SUFFIX_HOSPITAL})\b(?:[ \t]+(?:de|of)[ \t]+{NAME})?")),
    # lowercase type word after a capitalized name: "Sunrise Pediatric clinic", "Duke hospital"
    ("HOSPITAL", _c(rf"\b{ORG_NAME}(?:clinic|hospital|clínica|clinica)\b")),
    # Spanish order: Escuela Primaria Juárez, Guardería Los Pinitos, Hospital General de Oaxaca
    ("SCHOOL", _c(rf"\b(?:Escuela|Colegio|Instituto|Jardín de Niños|Jardin de Ninos|Kinder|Kínder|Preescolar)"
                  rf"(?:[ \t]+(?:Primaria|Secundaria|Elemental|Intermedia|Preparatoria))?[ \t]+{NAME}")),
    ("DAYCARE", _c(rf"\b(?:Guardería|Guarderia|Estancia Infantil|Centro de Cuidado Infantil|Centro de Desarrollo "
                   rf"Infantil|Centro Infantil|Daycare|Preescolar|Centro)[ \t]+(?!(?:de|del|Médico|Medico)\b){NAME}")),
    ("HOSPITAL", _c(rf"\b(?:Hospital|Clínica|Clinica|Sanatorio|Centro de Salud|Centro Médico)"
                    rf"(?:[ \t]+(?:General|Infantil|Regional|Civil|del Niño|de la Mujer))?[ \t]+(?:de[ \t]+)?{NAME}")),
]
# "born at WakeMed", "NICU at Duke", "nació en el Hospital ...", "seen by Dr. X", "referred by X"
_HOSPITAL_TRIGGER = _c(
    rf"\b(?i:born at|delivered at|NICU at|NICU stay at|admitted to|hospitalized at|transferred to|"
    rf"nació en|nacio en|UCIN de|UCIN en|internad[oa] en|hospitalizad[oa] en)[ \t]+(?i:el[ \t]+|la[ \t]+|the[ \t]+)?"
    rf"(?P<n>{ORG_NAME.replace('{1,5}', '{0,4}')}[{U}][\w'’&-]*)")
_PROVIDER_TRIGGER = _c(
    rf"\b(?i:seen by|followed by|referred by|evaluated by|referral from|pediatrician|PCP|"
    rf"physician|neurologist|audiologist|referido por|referida por|evaluad[oa] por|pediatra|neurólog[oa])"
    rf"[ \t]*[:,]?{_H}(?i:Dr\.?{_H}|Dra\.?{_H}|the{_H}|el{_H}|la{_H})?(?P<n>{NAME})")
_ORG_TRIGGER = _c(
    rf"\b(?i:(?:works|working|worked|employed|trabaja|trabajaba|labora)(?:\s+(?:nights|days|"
    rf"part-time|full-time|de noche|de día|medio tiempo))?[ \t]+(?:at|for|en|para)[ \t]+(?:the[ \t]+|el[ \t]+|la[ \t]+)?)"
    rf"(?P<n>{NAME})")
_AGENCY_NAMES = (r"DSS|CPS|WIC|Smart Start|Head Start|Early Head Start|Child Protective Services|"
                 r"Department of Social Services|Social Services|Servicios Sociales|Partnership for Children|"
                 r"Family Support Network|Children's Developmental Services Agency|CDSA|Departamento de Servicios "
                 r"Sociales|Programa WIC|Early Intervention Program|Programa de Intervención Temprana|"
                 r"Agencia de Servicios de Desarrollo Infantil|Centro de Desarrollo Infantil")
_DETERMINER = r"(?!(?:The|A|An|El|La|Los|Las|Our|Your|This|That|These|Their|His|Her|Su|Sus|Each|Every|Any|Both)\b)"
# Agencies (SPEC 6: "hide the name and keep the type"): bare names (DSS, WIC, CDSA, Smart Start),
# and office forms: "Henderson County CDSA", "Guilford County WIC Program", "CDSA de la Región del
# Piedmont", "DSS del Condado de Catawba", "Departamento de Servicios Sociales del Condado de X".
_AGENCY_OFFICE = _c(
    rf"\b(?:(?:{_DETERMINER}{NAME_TOKEN}(?:[ \t-]+{NAME_TOKEN})?[ \t]+(?:County[ \t]+)?)?(?:{_AGENCY_NAMES})"
    rf"(?:[ \t]+\([A-Z]{{2,6}}\))?(?:[ \t]+(?:Program|Programa|Office|Oficina|Regional))?"
    rf"(?:[ \t]+(?:of|de|del|for)[ \t]+(?:the[ \t]+|el[ \t]+|la[ \t]+|las[ \t]+|los[ \t]+)?"
    rf"(?:(?i:condado)[ \t]+de[ \t]+)?{NAME}(?:[ \t]+County)?)?"
    rf"|(?:CDSA|WIC|DSS)[ \t]+(?!(?:and|y|or|o|is|es|in|en)\b){NAME_TOKEN}(?:[ \t]+(?:\d{{1,2}}|{NAME_TOKEN}))?)\b")
_AGENCY = _c(rf"\b(?:{_AGENCY_NAMES})\b")
# Long Spanish agency names (dev3): "Agencia de Coordinación de Servicios para la Primera
# Infancia del Piamonte (ACSPIP)", "Programa Regional de Intervención Temprana y Apoyo a la
# Familia (PRITAF)". Sure with a bracketed acronym or a place in the name, else a check item.
_ES_AGENCY_HEAD = (r"Agencia|Programa|Secretar[ií]a|Centro|Red|Alianza|Coalici[oó]n|Fundaci[oó]n|"
                   r"Asociaci[oó]n|Oficina|Departamento|Instituto|Consejo|Servicios")
_ES_TOKEN = rf"[{U}][\w'’-]*"
_ES_JOIN = (r"(?:[ ]+(?:de|del|de[ ]la|de[ ]las|de[ ]los|para|para[ ]la|para[ ]el|para[ ]los|para[ ]las|con|y|e|"
            r"a[ ]la|al|en|en[ ]el|en[ ]la)[ ]+|[ ]+)")
_ES_AGENCY_LONG = _c(rf"\b(?:{_ES_AGENCY_HEAD})(?:{_ES_JOIN}{_ES_TOKEN}){{2,14}}(?P<acr>[ ]*\([A-Z][A-Z-]{{1,9}}\))?")
# An agency acronym with its county or town: "CSF de Wake", "ACSI de Guilford", "CDSA Piedmont Central"
_ACR_PLACE = _c(rf"\b(?P<a>[A-Z]{{2,6}})[ ]+(?:de[ ]+|del[ ]+)?(?P<p>[{U}][\w'’-]+(?:[ ]+[{U}][\w'’-]+)?)")
# The state's early intervention program, labelled an agency like the CDSA (DECISIONS 25)
_ITP = _c(r"\b(?:(?:NC|N\.C\.|North[ ]Carolina)(?:['’]s)?[ ]+)?Infant[- ]Toddler[ ]Program\b|"
          r"\bPrograma[ ]de[ ]Infantes[ ]y[ ]Ni[ñn]os[ ]Peque[ñn]os(?:[ ]de[ ]Carolina[ ]del[ ]Norte)?\b")
_ZIP_AFTER_CITY = _c(r",[ \t]*(?P<city>[A-Z][\w.'’ -]{1,30}?),[ \t]*(?P<v>\d{5}(?:-\d{4})?)(?![\d-])")
# NC Medicaid health plans and LME/MCOs name the family's coverage.
_HEALTH_PLANS = _c(r"\b(?:Trillium(?: Health Resources)?|Vaya Health|Alliance Health|Partners Health Management|"
                   r"WellCare(?: of North Carolina)?|AmeriHealth Caritas(?: North Carolina)?|Carolina Complete Health|"
                   r"Healthy Blue|UnitedHealthcare Community Plan|Blue Cross(?: and)? Blue Shield of North Carolina|"
                   r"BCBSNC|EBCI Tribal Option)\b")
# "attends Sunny Path", "enrolled at Little Lambs", "referred to Bright Start", "a través de Familias Unidas"
_ORG_CONTEXT = _c(rf"\b(?i:attends|attending|attended|enrolled (?:at|in)|placed at|referred to|referral to|staff at|"
                  rf"teachers at|through|via|a través de|por parte de|asiste a|inscrit[oa] en|referid[oa] a)"
                  rf"[ \t]+(?:the[ \t]+|el[ \t]+|la[ \t]+)?(?P<n>{_DETERMINER}{NAME_TOKEN}(?:[ \t]+(?:of[ \t]+|de[ \t]+)?{NAME_TOKEN}){{1,4}})")
# "Autism Society of North Carolina", "Down Syndrome Association of Greater Charlotte"
_SOCIETY = _c(rf"\b{_DETERMINER}(?:{NAME_TOKEN}[ \t]+){{1,3}}(?:Society|Foundation|Association|Network|Alliance|"
              rf"Coalition|Council|Fundación|Asociación)[ \t]+(?:of|de|for|para)[ \t]+(?:the[ \t]+)?{NAME}")
# School districts: "Orange County Schools", "Davidson County Schools Exceptional Children's Program"
_DISTRICT = _c(rf"\b{_DETERMINER}{NAME_TOKEN}(?:[ \t]+{NAME_TOKEN})?[ \t]+(?:County|City)[ \t]+(?:Public[ \t]+)?"
               rf"Schools?(?:[ \t]+System)?(?:[ \t]+(?:Exceptional Children's|Preschool|Pre-K)[ \t]+Program)?")
_DISTRICT_ES = _c(rf"\bDistrito[ \t]+Escolar[ \t]+(?:del[ \t]+(?i:condado)[ \t]+de[ \t]+|de[ \t]+)?{NAME}")
# "Wake County", "Condado de Cumberland" (below state level: a place).
_COUNTY = _c(rf"\b{_DETERMINER}{NAME_TOKEN}(?:[ \t]+{NAME_TOKEN})?[ \t]+County\b|\b(?i:condado)[ \t]+de[ \t]+{NAME}")
# A named street with no house number: "Startown Road", "calle Brisa de Cedro".
_STREET_NAMED = _c(rf"\b{_DETERMINER}(?:{NAME_TOKEN}[ \t]+){{1,3}}(?:Street|Avenue|Road|Drive|Lane|Boulevard|Court|"
                   rf"Parkway|Circle|Trail|Highway)\b|\b(?i:calle|avenida)[ \t]+{NAME}")
# Initials: "M.F.R.", "J. Ortiz" (Safe Harbor: derivatives of names are identifiers).
_INITIALS = _c(rf"(?<![\w.])(?:[{U}]\.){{2,4}}(?![\w])|(?<![\w.])(?!(?:I|V|X)\.)[{U}]\.[ \t]+[{U}][{L}]+(?:-[{U}][{L}]+)?\b")
_NOT_INITIALS = frozenset("""
M.S. M.A. B.S. B.A. M.D. R.N. D.O. P.T. O.T. U.S. U.K. P.O. A.M. P.M. E.U. D.C. N.C. S.C. M.Ed. Ph.D.
Ed.D. Ed.S. A.A. A.S. C.P. N.P. P.A. L.P.N. EE.UU. E.E.U.U. U.S.A. A.D. B.C. R.D. M.P.H.
""".split())

# Countries and states are allowed under Safe Harbor; only places below state
# level are hidden.
_COUNTRY_SET = frozenset(x.strip() for x in """
united states, usa, us, america, mexico, méxico, guatemala, honduras, el salvador, nicaragua,
costa rica, panama, panamá, colombia, venezuela, ecuador, peru, perú, bolivia, chile, argentina,
uruguay, paraguay, cuba, puerto rico, dominican republic, república dominicana, spain, españa,
brazil, brasil, canada, canadá, haiti, north carolina, south carolina, virginia, georgia,
tennessee, florida, texas, california, new york, oaxaca, chiapas, guerrero, michoacán, michoacan,
jalisco, puebla, veracruz, guanajuato, zacatecas, chihuahua, sonora, durango, sinaloa, nayarit,
hidalgo, morelos, tlaxcala, tabasco, campeche, yucatán, yucatan, quintana roo, nuevo león,
nuevo leon, coahuila, tamaulipas, san luis potosí, san luis potosi, aguascalientes, querétaro,
queretaro, colima, baja california, estado de méxico
""".replace("\n", " ").split(",") if x.strip())
_PLACE_TRIGGER = _c(
    rf"\b(?i:near|outside(?: of)?|close to|cerca de|afuera de|en las afueras de|village of|town of|city of|"
    rf"oficina de|office in|sede de|clinic in|clínica en|"
    rf"aldea|vereda|pueblo|pueblito|caserío|caserio|cantón|canton|municipio de|"
    rf"born in|grew up in|raised in|nació en|nacio en|nacido en|nacida en|creció en|crecio en|"
    rf"originally from|from the city of|de la ciudad de|del pueblo de|en el pueblo de|lives in|living in|moved from|moved to|resides in|from the town of|vive en|vivía en|"
    rf"viven en|se mudó de|se mudaron de|se mudó a|es de|son de|originari[oa] de|pueblo de|"
    rf"pueblito de|ciudad de){_H}(?P<n>{NAME})")


_COUNTRY_TAIL = (r"Mexico|México|Guatemala|Honduras|El Salvador|Nicaragua|Costa Rica|Panama|Panamá|Colombia|"
                 r"Venezuela|Ecuador|Peru|Perú|Bolivia|Cuba|Dominican Republic|República Dominicana|Puerto Rico")
# "San Pedro Sula, Honduras", "Riobamba, Ecuador": the name before a country is a place.
_PLACE_COUNTRY = _c(rf"(?P<n>{NAME}),{_H}(?:{_COUNTRY_TAIL})\b")


def _name_ok(name: str) -> bool:
    from .generic import all_generic

    first = name.split()[0].lower()
    return first not in NOT_NAMES and len(name.strip()) >= 2 and not all_generic(name)


def _emit_name(text: str, m: re.Match[str], kind: str, note: str, group: str = "n") -> Iterator[Candidate]:
    s, e = m.span(group)
    if s < 0:
        return
    name = text[s:e]
    if not _name_ok(name):
        return
    # Do not run a name into the next sentence: stop at a token that starts a
    # new clause after a comma ("Lucía, dijo").
    yield Candidate(s, e, kind, "rules", True, 1 if kind not in ("PERSON",) else 0, note)


# SPEC 6: ages over 89 are identifiers ("abuela, 92 years old", "94;2", "tiene 91 años").
_AGE_90 = _c(r"(?<![\w;])(?:9\d|1[01]\d)(?:;\d{1,2}(?![\w;])|(?:[ -]years?[ -]old|[ -]year-old|[ \t]+años|"
             r"[ \t]+anos|[ \t]+yo\b|[ \t]+y/o\b))", _I)


# A capitalized name run (with connectors and dash segments) that contains an
# organization keyword anywhere: "Iglesia Bautista Hispana Nueva Esperanza",
# "Cary Public Library", "Duke Health Ear, Nose & Throat – Page Road Clinic".
_ORG_KEYWORDS = {
    "ORG": r"Library|Biblioteca|Church|Iglesia|Parish|Parroquia|Temple|Templo|Mosque|Ministry|Ministerio|"
           r"Plant|Planta|Farms?|Finca|Granja|Restaurant|Restaurante|Tienda|Store|Mart|Market|Mercado|Lumber|"
           r"Construction|Construcción|Constructora|Limpieza|Cleaning|Dairy|Factory|Fábrica|Warehouse|Bodega|"
           r"Community Center|Centro Comunitario|Centro|YMCA|Boys & Girls Club|Society|Sociedad",
    "HOSPITAL": r"Clinic|Clínica|Clinica|Hospital|Campus|ENT|Audiology|Audiología|Pediatrics|Pediatría|"
                r"Pediatria|Rehab|Rehabilitation|Medical|Médico|Medico",
    "AGENCY": r"Agency|Agencia|Partnership|Programa|Coordinación|Coordinacion|Oficina|Office|"
              r"Department|Departamento|Asociación|Asociacion|Fundación|Fundacion|Coalition",
    "DAYCARE": r"Daycare|Guardería|Guarderia|Preschool|Preescolar|Casita|Cottage|Clubhouse|Nest|Nido|"
               r"Learning|Kids|Niños|Ninos|Infantil|Montessori",
    "SCHOOL": r"School|Schools|Escuela|Escuelas|Academy|Academia|Elementary|Primaria|Colegio|Distrito Escolar|"
              r"School District",
}
_RUN_TOKEN = rf"(?:[{U}][\w'’.&-]*|&)"
# Spaces only: a tab separates table cells, and two cells are two values.
_RUN_JOIN = r"(?:[ ]+(?:de|del|de la|de los|de las|para|of|the|and|y)[ ]+|[ ]*[,–—-][ ]*|[ ]+)"
_ORG_RUN = _c(rf"\b{_NOT_START}{_RUN_TOKEN}(?:{_RUN_JOIN}{_RUN_TOKEN}){{1,9}}(?P<acr>[ \t]*\([A-Z]{{2,8}}\))?")
# Words of test names: a run holding one is a test, not an organization ("Preschool Language Scales, Fifth Edition")
_TEST_WORDS = re.compile(r"\b(?:Scales?|Test|Tests|Edition|Edición|Inventory|Inventario|Measures?|Battery|Batería|"
                         r"Screener|Checklist|Questionnaire|Survey|Prueba|Escala)\b")
_ORG_KEY_RES = [(k, _c(rf"\b(?:{v})\b")) for k, v in _ORG_KEYWORDS.items()]
# Single keywords as plain words ("school", "library"), and words of form labels and
# activities: a run made only of these names nothing.
_RUN_TYPE_WORDS = frozenset(w.lower() for v in _ORG_KEYWORDS.values() for w in v.split("|") if " " not in w
                            and w.isalpha())
_LABEL_WORDS = frozenset("""
attendance visit visits visita visitas hours horario schedule history historia readiness placement enrollment
enrolled inscripción inscripcion setting settings observation observations observación observacion activity
activities actividad actividades routine routines rutina rutinas day days día dia días dias time hora transition
transición transicion staff records report reporte contact contacto information información informacion info
name nombre address dirección direccion phone teléfono telefono type tipo status estado level nivel grade grado
year año ano start end begin date fecha notes nota notas plan meeting reunión reunion consultation consulta
""".split())
# "la planta empacadora de carne Smithfield", "el restaurante El Bajío", "servicio de limpieza Carolina"
_BUSINESS_NOUN = _c(
    rf"\b(?i:planta|fábrica|fabrica|granja|finca|restaurante|tienda|compañía|compania|empresa|constructora|"
    rf"servicio de limpieza|servicios de limpieza|panadería|panaderia|taller|lavandería|lavanderia|mercado|"
    rf"supermercado|hotel|bodega|iglesia|parroquia|biblioteca)(?:[ \t]+[{L}]+){{0,4}}[ \t]+(?P<n>{_RUN_TOKEN}"
    rf"(?:{_RUN_JOIN}{_RUN_TOKEN}){{0,5}})")


# Words that make a run of type words a description, not a name: "Preschool
# Classroom", "Early Learning services", "Daycare Program".
_DESCRIPTOR_WORDS = frozenset("""
classroom classrooms room rooms services service program programs programa curriculum activities activity
setting settings environment skills time model approach approaches staff level class clase aula salón salon
""".split())


# Descriptor heads of activities and methods: a phrase ending in one is text
# ("Exploring Learning Approaches"); heads that can name an organization
# ("Program", "Services", "Center") are not here, so such phrases stay reviewable.
_ACTIVITY_HEADS = frozenset("""
approach approaches activities activity skills curriculum time setting settings environment level model
models strategies strategy techniques technique methods method actividades estrategias técnicas tecnicas
enfoque enfoques
""".split())

_AFTER_DESCRIPTORS = frozenset("services service program programs programa curriculum classroom classrooms "
                               "activities approach".split())


def _name_class(text: str, start: int, end: int) -> str:
    """How a capitalized run with an organization word should be treated:
    "skip" (no name: "Mental Health", "School Attendance", "Preschool Classroom",
    "Early Learning services"), "unsure" (only type and generic words, so it may
    be a name: "Kids Academy" becomes a check item, hidden by default), or "name"
    (a distinct name word: "Cary Public Library")."""
    words = [w.lower() for w in re.findall(r"[^\W\d_]+", text[start:end])]

    def base(w: str) -> bool:
        return w in _CLINICAL_PHRASE_WORDS or w in _GENERIC_ORG_WORDS or all_generic(w)

    if not words or all(base(w) for w in words):
        return "skip"
    if not all(base(w) or w in _RUN_TYPE_WORDS or w in _LABEL_WORDS or w in _DESCRIPTOR_WORDS for w in words):
        return "name"
    after = re.match(r"[ \t]+([a-záéíóúñü]+)", text[end:end + 30])
    if words[-1] in _LABEL_WORDS or words[-1] in _DESCRIPTOR_WORDS:
        return "skip"  # the head is a label or descriptor: "School Attendance", "Preschool Classroom"
    # "Early Learning services": a generic modifier plus a following descriptor is a
    # description; "Kids Academy services" has no generic word and may be a name.
    if after and after.group(1) in _AFTER_DESCRIPTORS and any(
            w in _GENERIC_ORG_WORDS or w in _CLINICAL_PHRASE_WORDS for w in words):
        return "skip"
    return "unsure"


# Positive evidence that a comma tail belongs to the organization: a keyword, a
# location word, or a known place ("Maplecrest Clinic, Oakwood Branch", "Duke
# Health, Durham"). Without it the tail is text: "Maplecrest Clinic, Hearing Loss".
_LOCATION_WORDS = re.compile(r"\b(?:Branch|Location|Site|Office|Campus|Annex|Satellite|Unit|Division|Wing|"
                             r"Building|Suite|Sucursal|Sede|Oficina|Nose|Throat)\b")
_LEGAL_TAIL = re.compile(r"^[ \t]*(?:PLLC|LLC|L\.L\.C\.|Incorporated|Incorporada|Incorporado|Inc\.?|P\.C\.|"
                         r"P\.A\.|Corp\.?|Co\.|S\.A\.)[ \t.]*$")


_FINDING_WORDS = frozenset("""
normal abnormal typical atypical mild moderate severe profound delayed delay within limits functional passed
failed pass fail referred results result findings finding intact unremarkable bilateral unilateral left right
adequate appropriate impaired impairment loss reduced concerns concern noted reported improved emerging
screening evaluation assessment report history records summary disorder disorders
normales anormal leve moderado moderada severo severa retraso adecuado adecuada resultados resultado
hallazgos evaluación evaluacion historia trastorno pérdida perdida
""".split())


def _plain_word(w: str) -> bool:
    w = w.lower()
    return (w in _FINDING_WORDS or w in _LABEL_WORDS or w in _CLINICAL_PHRASE_WORDS or w in _GENERIC_ORG_WORDS
            or w in _RUN_TYPE_WORDS or w in _DESCRIPTOR_WORDS or all_generic(w))


def org_segment_ok(seg: str) -> bool:
    """A comma segment inside an organization's run: one with evidence
    (_tail_belongs), or generic organization words with no finding word
    ("Audiology Department" yes, "Hearing Loss" no)."""
    if _tail_belongs(seg):
        return True
    words = [w.lower() for w in re.findall(r"[^\W\d_]+", seg)]
    return bool(words) and not any(w in _FINDING_WORDS for w in words) and all(
        w in _GENERIC_ORG_WORDS or w in _RUN_TYPE_WORDS or all_generic(w) for w in words)


def _tail_belongs(seg: str) -> bool:
    """A known place, or a keyword or location word beside a distinct word:
    "Oakwood Branch", "Page Road Clinic", "Durham" belong; "Audiology Results",
    "Medical History", "Hearing Loss" do not."""
    return _tail_status(seg) == "yes"


def _tail_status(seg: str) -> str:
    """"yes" (a place, or a keyword or location word beside a distinct word),
    "maybe" (evidence, but the only distinct words are prose-shaped: "Blessed
    Church"), or "no" ("Audiology Results", "… Center Schedule")."""
    from .gazetteer import is_place

    if _LEGAL_TAIL.match(seg):
        return "yes"
    toks = [t.rstrip(".") for t in re.findall(r"[^\W\d_][\w'’.-]*", seg)]
    if not toks:
        return "no"
    if is_place(" ".join(toks)) or any(is_place(t) for t in toks):
        return "yes"
    if toks[-1].lower() in _LABEL_WORDS or toks[-1].lower() in _ACTIVITY_HEADS:
        return "no"  # "…, Shared Community Center Schedule", "…, Exploring Learning Approaches"
    if not (any(kp.search(seg) for _, kp in _ORG_KEY_RES) or _LOCATION_WORDS.search(seg)):
        return "no"
    # Finding-like words are plain ("Normal", "Loss"), so a distinct word must carry the name:
    # "Normal Heights Church" yes, "Audiology Results" no.
    distinct = [t for t in toks if not _plain_word(t)]
    if any(not _boundary_word(t) for t in distinct):
        return "yes"
    return "maybe" if distinct else "no"


def _boundary_word(w: str) -> bool:
    """Prose-like words that are not name evidence on their own ("Shared",
    "Exploring", "Attends")."""
    low = w.lower()
    return low in _PROSE_STARTERS or low in _NAMEY_PROSE or (len(low) > 4 and low.endswith(("ed", "ing")))


_RUN_ABBREVIATIONS = frozenset("St Mt Ft Dr Dra Jr Sr Sra Inc Co Corp Ltd Bros Ave Blvd Rd No Univ Assn Dept".split())


def kp_type(word: str) -> bool:
    """A single organization type keyword ("Programa", "Agencia", "Centro")."""
    return word.lower() in _RUN_TYPE_WORDS or bool(re.fullmatch(_ES_AGENCY_HEAD, word))


def _sentence_start(text: str, pos: int) -> bool:
    i = pos - 1
    while i >= 0 and text[i] in " \t\"“'‘(¿¡*•-":
        i -= 1
    return i < 0 or text[i] in ".!?\n:;"


# Words that open a sentence of prose before an organization name: "Attends Little
# Sprouts Daycare", "Visits Cary Public Library", "Staff Training Center". Only these
# are trimmed; an unlisted first word may be part of the name ("Maplecrest Children's
# Hospital called").
_PROSE_STARTERS = frozenset("""
attends attended attending visits visited visiting uses used goes went going called calls saw seen sees
spoke speaks met meets contacted contacts referred refers recommended recommends reports reported
enrolled enrolls joined joins started starts left leaves moved moves returned returns transferred
staff today yesterday tomorrow currently previously recently also then now later per
asiste asistió asistio visita visitó visito usa va fue llamó llamo vio habló hablo conoció conocio refirió
refirio recomendó recomendo reporta reportó reporto empezó empezo comenzó comenzo dejó dejo regresó regreso
hoy ayer mañana manana actualmente anteriormente recientemente también tambien luego ahora
discussed discusses received receives reviewed reviews observed observes evaluated evaluates provided
provides requested requests completed completes scheduled schedules notified notifies emailed faxed sent
sends shared shares consulted consults coordinated coordinates explained explains confirmed confirms
discutió discutio recibe recibió recibio revisó reviso observó observo evaluó evaluo proporcionó proporciono
solicitó solicito completó completo programó programo notificó notifico envió envio compartió compartio
consultó consulto coordinó coordino explicó explico confirmó confirmo
discussing receiving reviewing observing evaluating providing requesting completing scheduling contacting
attended visited called met spoke phoned emailed texted messaged recommended suggested advised
informed told asked agreed declined approved denied submitted forwarded obtained gathered collected
documented noted indicated stated described expressed shared mentioned visited toured checked
llamamos visitamos hablamos recibimos revisamos enviamos contactamos acordamos solicitamos
contactó contacto informó informo indicó indico mencionó menciono expresó expreso describió describio
""".split())


def _prose_word(word: str) -> bool:
    """A sentence-opening prose word from the explicit list (used for one-word
    runs; _trim_initial also treats "-ed"/"-ing" words as ambiguous boundaries)."""
    if any(kp.fullmatch(word) for _, kp in _ORG_KEY_RES):
        return False  # "Learning Academy": a keyword is part of the name
    return word.lower() in _PROSE_STARTERS


# Listed prose words that also open organization names ("Shared Beginnings Daycare",
# "Coordinated Care Clinic", "Going Places Daycare").
_NAMEY_PROSE = frozenset("""
shared coordinated going united blessed gifted inspired advanced integrated connected combined associated
allied certified preferred trusted growing shining caring loving helping giving living healing sharing
building rising
""".split())


def _trim_initial(text: str, s: int, e: int) -> tuple[int, tuple[int, int] | None, str | None]:
    """Start of an organization span without a sentence-initial prose word, the
    span of an unsure check item for that word, and an override class for the
    organization ("skip" or "unsure", else None).

    A listed prose word ("Attends") is trimmed. An ambiguous one, which can also
    open a name (_NAMEY_PROSE, a listed name such as "Ayer", or an "-ed"/"-ing"
    word such as "Exploring" or "Shining"), is trimmed only if a name follows
    and comes back as an unsure item. If what follows holds a finding word or
    ends in a label ("Shared Medical History", "… Center Schedule"), this group
    is not a name; otherwise ("Shared Care Clinic", "Blessed Day School") the
    whole span is kept as unsure."""
    from .gazetteer import is_listed

    if not _sentence_start(text, s):
        return s, None, None
    rest = re.match(r"([^\W\d_][\w'’-]*)[ \t]+", text[s:e])
    if not rest or any(kp.fullmatch(rest.group(1)) for _, kp in _ORG_KEY_RES):
        return s, None, None
    word, low = rest.group(1), rest.group(1).lower()
    shaped = len(low) > 4 and low.endswith(("ed", "ing"))
    if low not in _PROSE_STARTERS and not shaped:
        return s, None, None
    if low in _PROSE_STARTERS and low not in _NAMEY_PROSE and not is_listed(word):
        return s + rest.end(), None, None
    after = _name_class(text, s + rest.end(), e)
    if after == "name":
        return s + rest.end(), (s, s + len(word)), None
    words = [w.lower() for w in re.findall(r"[^\W\d_]+", text[s + rest.end():e])]
    named = re.match(r"[ \t]*\([A-Z][A-Z&]{1,7}\)", text[e:e + 12]) is not None  # "(ILP)" after it
    if any(w in _FINDING_WORDS for w in words) or (
            not named and words and (words[-1] in _LABEL_WORDS or words[-1] in _ACTIVITY_HEADS)):
        return s, None, "skip"  # "Shared Medical History", "Shared Community Center Schedule"
    # "Blessed Day School", "Shared Care Clinic", "Inspired Learning Program (ILP)": maybe a name
    return s, None, "unsure"


def _initial_item(span: tuple[int, int] | None) -> list[Candidate]:
    """The unsure check item for a trimmed prose word that may be a name; only
    emitted together with its organization."""
    if span is None:
        return []
    return [Candidate(span[0], span[1], "PERSON", "rules", False, 0, "rule:org-initial-name")]


def _org_runs(text: str) -> list[Candidate]:
    out = []
    scan = 0
    while (m := _ORG_RUN.search(text, scan)) is not None:
        scan = m.end()
        end0 = m.start("acr") if m.group("acr") else m.end()
        run = text[m.start():end0]
        # A run never crosses a sentence end: "Duke Hospital. He has ..." stops at "Hospital",
        # and the rest is scanned again ("Maplecrest Clinic. Oakwood Church called.").
        # Abbreviations and initials keep their period ("St. Mary's", "Smith Co.", "J. P.").
        for p_ in re.finditer(r"([^\W\d_][\w'’-]*)\.(?=[ \t]|$)", run):
            if p_.group(1) not in _RUN_ABBREVIATIONS and len(p_.group(1)) > 1:
                run = run[:p_.end(1)]
                scan = m.start() + p_.end(1) + 1
                break
        run = run.rstrip(" ,–—-")
        start = m.start()
        # A sentence-initial word that is not a name, a place, or a keyword is not part
        # of the run ("Visits Cary Public Library", "Staff ..."): trim it, but only if a
        # distinct name remains ("Oakwood Church called" keeps "Oakwood").
        if not re.search(r"[ \t]", run) and _sentence_start(text, start) and _prose_word(run):
            continue
        # judged within the first comma group: "Shining Church, Reduced Hearing, Oakwood Branch"
        new_start, initial, override = _trim_initial(text, start, start + len(run.split(",")[0]))
        run, start = run[new_start - start:], new_start
        # A comma tail needs positive evidence to stay in the name (_tail_belongs).
        # Comma segments: the first, plus each later segment with evidence (_tail_belongs).
        # Contiguous ones form one name; a segment without evidence stays text and splits
        # the run: "Maplecrest Clinic, Reduced Hearing, Page Road Clinic" gives two names.
        pieces, pos = [], 0
        for i, sg in enumerate(run.split(",")):
            # a clinical first group ("Shared Medical History, Oakwood Church") is text;
            # a "maybe" segment ("Blessed Church") is its own unsure group
            status = ("yes" if override != "skip" else "no") if i == 0 else _tail_status(sg)
            if status == "yes" and pieces and pieces[-1][2] == "yes":
                pieces[-1][1] = pos + len(sg)
            else:
                pieces.append([pos, pos + len(sg), status])
            pos += len(sg) + 1
        groups = [(start + a, start + b, st) for a, b, st in pieces if st != "no"]
        first_kind = None
        for gi, (gs, ge, status) in enumerate(groups):
            seg = text[gs:ge]
            lead = len(seg) - len(seg.lstrip(" –—-"))
            gs, ge = gs + lead, gs + len(seg.rstrip(" ,–—-"))
            piece = text[gs:ge]
            words = re.findall(r"[^\W\d_]+", piece)
            if not words or _TEST_WORDS.search(piece) or (gi == 0 and len(words) < 2):
                continue
            kind = next((k for k, kp in _ORG_KEY_RES if kp.search(piece)), first_kind)
            first_kind = first_kind or kind  # kept even when this piece is generic ("Audiology Department")
            cls = _name_class(text, gs, ge)
            if cls == "name" and (status == "maybe" or (gi == 0 and override == "unsure")):
                cls = "unsure"
            if cls == "skip" or kind is None:
                continue
            out.append(Candidate(gs, ge, kind, "rules", cls == "name", 0,
                                 "rule:org-run" + ("" if cls == "name" else ":ambiguous")))
            if gi == 0:
                out += _initial_item(initial)
        # A bracketed acronym after the name ("(CDDP)") is added in pipeline.detect for
        # every organization candidate, with the type of the strongest one.
    for m in _BUSINESS_NOUN.finditer(text):
        name = m.group("n").rstrip(" ,–—-")
        if not all_generic(name):
            out.append(Candidate(m.start("n"), m.start("n") + len(name), "ORG", "rules", True, 0,
                                 "trigger:business-noun"))
    return out


def find(text: str) -> list[Candidate]:
    out: list[Candidate] = []
    out.extend(_org_runs(text))
    for m in _AGE_90.finditer(text):
        out.append(Candidate(m.start(), m.end(), "AGE", "rules", True, 0, "rule:age-over-89"))
    for p in DATE_PATTERNS:
        for m in p.finditer(text):
            out.append(Candidate(m.start(), m.end(), "DATE", "rules", True, 0, "rule:date"))
    for p in TIME_PATTERNS:
        for m in p.finditer(text):
            out.append(Candidate(m.start(), m.end(), "TIME", "rules", True, 0, "rule:time"))
    for p in PHONE_PATTERNS:
        for m in p.finditer(text):
            out.append(Candidate(m.start(), m.end(), "PHONE", "rules", True, 0, "rule:phone"))
    for m in _PHONE_WORD_7.finditer(text):
        out.append(Candidate(m.start(1), m.end(1), "PHONE", "rules", True, 0, "rule:phone"))
    for m in EMAIL.finditer(text):
        out.append(Candidate(m.start(), m.end(), "EMAIL", "rules", True, 0, "rule:email"))
    for m in URL.finditer(text):
        out.append(Candidate(m.start(), m.end(), "URL", "rules", True, 0, "rule:url"))
    for m in DOMAIN.finditer(text):
        out.append(Candidate(m.start(), m.end(), "URL", "rules", True, 0, "rule:domain"))
    for p in ADDRESS_PATTERNS:
        for m in p.finditer(text):
            s, e = m.span()
            frag = text[s:e].strip(" ,")
            if frag.lower() in _COUNTRY_SET:
                continue
            out.append(Candidate(s, s + len(text[s:e].rstrip(" ,")), "PLACE", "rules", True, 0, "rule:address"))
    for m in ZIP_AFTER_STATE.finditer(text):
        out.append(Candidate(m.start("v"), m.end("v"), "PLACE", "rules", True, 0, "rule:zip"))
    for m in _ZIP_AFTER_CITY.finditer(text):  # "204 Birchwood Lane, Raleigh, 27610"
        from .gazetteer import is_place as _is_place

        if _is_place(m.group("city").strip()):
            out.append(Candidate(m.start("v"), m.end("v"), "PLACE", "rules", True, 0, "rule:zip"))
    for m in ZIP_LABEL.finditer(text):
        out.append(Candidate(m.start(1), m.end(1), "PLACE", "rules", True, 0, "rule:zip"))
    for kind, p in ID_PATTERNS:
        for m in p.finditer(text):
            out.append(Candidate(m.start(), m.end(), kind, "rules", True, 0, "rule:id"))
    for m in _ID_AFTER_LABEL.finditer(text):
        label = text[m.start():m.start("v")]
        kind = "MEDICAID_ID" if _MEDICAID_LABEL.search(label) else "ID"
        out.append(Candidate(m.start("v"), m.end("v"), kind, "rules", True, 0, "rule:id-label"))
    for role, p in _KIN_RES:
        for m in p.finditer(text):
            out.extend(_emit_name(text, m, role, f"trigger:{role.lower()}"))
    for role, p in _HON_RES:
        for m in p.finditer(text):
            out.extend(_emit_name(text, m, role, f"trigger:honorific-{role.lower()}"))
    for m in _NICKNAME.finditer(text):
        out.extend(_emit_name(text, m, "PERSON", "trigger:nickname"))
    for m in _PET_APPOSITIVE.finditer(text):
        out.extend(_emit_name(text, m, "PET", "trigger:pet-appositive"))
    for m in _CLASS_OF.finditer(text):
        out.extend(_emit_name(text, m, "TEACHER", "trigger:class-of"))
    for kind, p in _ORG_RES:
        for m in p.finditer(text):
            s, e = m.span()
            s, initial, override = _trim_initial(text, s, e)
            cls = "skip" if override == "skip" else _name_class(text, s, e)
            if override == "unsure" and cls == "name":
                cls = "unsure"
            if cls == "skip":
                continue  # "Mental Health", "Daycare Schedule": a topic or a label, not a provider
            out.append(Candidate(s, e, kind, "rules", cls == "name", 0,
                                 f"rule:{kind.lower()}" + ("" if cls == "name" else ":ambiguous")))
            out += _initial_item(initial)
    for m in _HEALTH.finditer(text):
        out.append(Candidate(m.start(), m.end(), "HOSPITAL", "rules", True, 0, "rule:health-brand"))
    for m in _HOSPITAL_TRIGGER.finditer(text):
        out.append(Candidate(m.start("n"), m.end("n"), "HOSPITAL", "rules", True, 0, "trigger:hospital"))
    for m in _PROVIDER_TRIGGER.finditer(text):
        out.extend(_emit_name(text, m, "PROVIDER", "trigger:provider"))
    for m in _ORG_TRIGGER.finditer(text):
        out.extend(_emit_name(text, m, "ORG", "trigger:workplace"))
    from .gazetteer import is_place

    for m in _ES_AGENCY_LONG.finditer(text):
        end = m.start("acr") if m.group("acr") else m.end()
        name = text[m.start():end]
        if _TEST_WORDS.search(name):
            continue
        toks = re.findall(r"[^\W\d_][\w'’-]*", name)
        placed = any(is_place(t) for t in toks) or re.search(
            r"\b(?:Condado|Piamonte|Piedmont|Carolinas?|Triad|Tr[ií]angulo|Triangle)\b", name)
        sure = bool(m.group("acr")) or bool(placed)
        if not sure and all(w.lower() in _CLINICAL_PHRASE_WORDS or w.lower() in _GENERIC_ORG_WORDS
                            or all_generic(w) or kp_type(w) for w in toks):
            continue  # "Programa de Terapia del Habla": a service, not a named agency
        out.append(Candidate(m.start(), end, "AGENCY", "rules", sure, 0,
                             "rule:agency-es" + ("" if sure else ":ambiguous")))
    from .protect import protect as _protect

    for m in _ACR_PLACE.finditer(text):
        place = m.group("p")
        if _protect(m.group("a")):
            continue  # "OT Raleigh", "ABA Durham": a clinical abbreviation, not an agency
        if is_place(place) or is_place(place.split()[0]) or re.fullmatch(
                r"(?:Piedmont|Triangle|Triad|Sandhills|Suroeste|Southwest|Northeast|Noreste)(?:[ ]+Central)?", place):
            out.append(Candidate(m.start(), m.end(), "AGENCY", "rules", True, 0, "rule:agency-acronym-place"))
    for m in _ITP.finditer(text):
        out.append(Candidate(m.start(), m.end(), "AGENCY", "rules", True, 0, "rule:agency-itp"))
    for m in _AGENCY.finditer(text):
        out.append(Candidate(m.start(), m.end(), "AGENCY", "rules", True, 0, "rule:agency"))
    for m in _HEALTH_PLANS.finditer(text):
        out.append(Candidate(m.start(), m.end(), "AGENCY", "rules", True, 0, "rule:health-plan"))
    for m in _ORG_CONTEXT.finditer(text):
        words = re.findall(r"[^\W\d_]+", m.group("n"))
        if not all(w.lower() in _CLINICAL_PHRASE_WORDS or w.lower() in _GENERIC_ORG_WORDS or all_generic(w)
                   for w in words):
            out.append(Candidate(m.start("n"), m.end("n"), "ORG", "rules", True, 0, "trigger:org-context"))
    for m in _SOCIETY.finditer(text):
        out.append(Candidate(m.start(), m.end(), "ORG", "rules", True, 0, "rule:society"))
    for m in _DISTRICT_ES.finditer(text):
        out.append(Candidate(m.start(), m.end(), "SCHOOL", "rules", True, 0, "rule:district"))
    for m in _DISTRICT.finditer(text):
        out.append(Candidate(m.start(), m.end(), "SCHOOL", "rules", True, 0, "rule:district"))
    for m in _COUNTY.finditer(text):
        out.append(Candidate(m.start(), m.end(), "PLACE", "rules", True, 0, "rule:county"))
    for m in _STREET_NAMED.finditer(text):
        out.append(Candidate(m.start(), m.end(), "PLACE", "rules", True, 0, "rule:street"))
    for m in _INITIALS.finditer(text):
        if m.group().replace(" ", "") not in _NOT_INITIALS:
            out.append(Candidate(m.start(), m.end(), "PERSON", "rules", True, 0, "rule:initials"))
    for m in _AGENCY_OFFICE.finditer(text):
        out.append(Candidate(m.start(), m.end(), "AGENCY", "rules", True, 0, "rule:agency-office"))
    for m in _TARGET_STORE.finditer(text):
        g = next(k for k in ("n", "w", "s") if m.group(k))
        out.append(Candidate(m.start(g), m.end(g), "ORG", "rules", True, 0, "rule:company"))
    for p in _COMPANY_RES:
        for m in p.finditer(text):
            words = re.findall(r"[^\W\d_]+", m.group())
            if all(w.lower() in _GENERIC_ORG_WORDS or all_generic(w) for w in words):
                continue  # "Speech Therapy Services", "Terapia del habla y lenguaje": headings
            out.append(Candidate(m.start(), m.end(), "ORG", "rules", True, 0, "rule:company"))
    for m in _PLACE_COUNTRY.finditer(text):
        if m.group("n").lower() not in _COUNTRY_SET:
            out.append(Candidate(m.start("n"), m.end("n"), "PLACE", "rules", True, 0, "rule:place-country"))
    for m in _PLACE_TRIGGER.finditer(text):
        name = m.group("n")
        if name.lower() in _COUNTRY_SET:
            continue
        out.append(Candidate(m.start("n"), m.end("n"), "PLACE", "rules", True, 0, "trigger:place"))
    return out
