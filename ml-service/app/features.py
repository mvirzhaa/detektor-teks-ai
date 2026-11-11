"""
Ekstraksi fitur teks: segmentasi kalimat/segmen dan fitur stilometri.

Fitur stilometri menangkap "gaya" tulisan yang sering membedakan teks AI dan
manusia, misalnya variasi panjang kalimat (burstiness), keragaman kosakata,
frasa transisi formal, dan penanda bahasa informal. Fitur-fitur ini digabung
dengan n-gram kata/karakter di model.py.
"""
from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

WORD_RE = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)
# Kalimat: potongan teks sampai tanda akhir kalimat atau baris baru
SENT_RE = re.compile(r"[^.!?\n]+(?:[.!?]+[\"'”’)\]]*|\n|$)")
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]", re.UNICODE
)
LIST_LINE_RE = re.compile(r"^\s*(?:[-*•▪◦]|\d+[.)]|[a-zA-Z][.)])\s+", re.MULTILINE)

FUNCTION_WORDS = set(
    """
    yang dan di ke dari ini itu dengan untuk pada adalah dalam tidak akan juga atau
    oleh sebagai karena bahwa ada dapat bisa sudah telah masih lebih para saat secara
    agar namun tetapi tapi jika kalau maka serta hingga sampai bagi tentang seperti
    the a an and or but of to in on at for with by from as is are was were be been
    that this these those it its not no can will would should could has have had do
    """.split()
)

TRANSITIONS = [
    # Indonesia
    "selain itu", "oleh karena itu", "dengan demikian", "di sisi lain", "namun demikian",
    "lebih lanjut", "pada akhirnya", "secara keseluruhan", "sebagai kesimpulan",
    "dalam hal ini", "dengan kata lain", "pertama", "kedua", "ketiga", "terakhir",
    "selanjutnya", "akhirnya", "oleh sebab itu", "sementara itu", "di samping itu",
    "maka dari itu", "hal ini", "tidak hanya itu",
    # Inggris
    "furthermore", "moreover", "additionally", "in addition", "however", "therefore",
    "consequently", "in conclusion", "overall", "on the other hand", "ultimately",
    "firstly", "secondly", "finally", "as a result", "in summary", "notably",
]

AI_PHRASES = [
    # Indonesia
    "penting untuk", "perlu diingat", "secara keseluruhan", "dapat disimpulkan",
    "memainkan peran penting", "peran yang sangat penting", "berbagai aspek",
    "dalam era", "era digital", "di era modern", "tidak dapat dipungkiri",
    "seiring dengan perkembangan", "memberikan dampak", "dampak yang signifikan",
    "secara signifikan", "aspek penting", "upaya untuk", "menjadi salah satu",
    "salah satu faktor", "sangat penting untuk", "dengan berbagai", "pendekatan yang",
    "komprehensif", "holistik", "optimal", "berkelanjutan", "inovatif",
    "mari kita", "semoga bermanfaat", "kesimpulannya",
    # Inggris
    "it is important to", "it's important to", "plays a crucial role", "crucial role",
    "in today's", "delve", "tapestry", "landscape of", "a testament to", "navigate",
    "multifaceted", "comprehensive", "furthermore", "in conclusion", "overall,",
    "it is worth noting", "seamless", "leverage", "robust", "foster",
]

INFORMAL = set(
    """
    gak nggak ngga enggak ga gk tdk yg dgn utk krn aja aj sih dong deh kok lho loh
    kan banget bgt bener beneran udah udh sdh gue gw gua lu lo elu kalo klo emang
    emg kayak kyk kaya gimana gmn kenapa knp ngapain wkwk wkwkwk haha hehe hihi anjir
    anjay mantap mantul btw otw gaes guys bro sis cuy njir duh wah ya kak min
    lol lmao gonna wanna gotta kinda yeah yep nope dunno ok okay omg tbh idk
    """.split()
)

FIRST_PERSON = set("saya aku gue gw gua kami kita i me my mine we us our".split())


def normalize_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def words(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text)]


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Kembalikan (awal, akhir) setiap kalimat dalam teks asli."""
    spans = []
    for m in SENT_RE.finditer(text):
        s, e = m.start(), m.end()
        chunk = text[s:e]
        lstrip = len(chunk) - len(chunk.lstrip())
        rstrip = len(chunk) - len(chunk.rstrip())
        s2, e2 = s + lstrip, e - rstrip
        if e2 > s2 and WORD_RE.search(text[s2:e2]):
            spans.append((s2, e2))
    return spans


def sentences(text: str) -> list[str]:
    return [text[s:e] for s, e in sentence_spans(text)]


def segment_spans(text: str, target_words: int = 70, max_words: int = 130) -> list[tuple[int, int]]:
    """Gabungkan kalimat menjadi segmen ±target_words kata untuk analisis per bagian."""
    spans = sentence_spans(text)
    segs: list[tuple[int, int]] = []
    cur_start, cur_end, cur_words = None, None, 0
    for s, e in spans:
        n = len(WORD_RE.findall(text[s:e]))
        para_break = cur_end is not None and "\n\n" in text[cur_end:s]
        if cur_start is not None and (
            cur_words + n > max_words or (para_break and cur_words >= target_words * 0.5)
            or cur_words >= target_words
        ):
            segs.append((cur_start, cur_end))
            cur_start, cur_words = None, 0
        if cur_start is None:
            cur_start = s
        cur_end = e
        cur_words += n
    if cur_start is not None:
        # segmen terakhir yang terlalu pendek digabung ke segmen sebelumnya
        if segs and cur_words < target_words * 0.35:
            segs[-1] = (segs[-1][0], cur_end)
        else:
            segs.append((cur_start, cur_end))
    return segs


def chunk_text(text: str, target_words: int = 110) -> list[str]:
    """Potongan teks untuk augmentasi data latih (dokumen panjang)."""
    return [text[s:e] for s, e in segment_spans(text, target_words, int(target_words * 1.6))]


def detect_language(text: str) -> str:
    w = words(text[:3000])
    id_hits = sum(1 for x in w if x in {"yang", "dan", "di", "ini", "itu", "dengan", "untuk", "tidak", "adalah", "dari"})
    en_hits = sum(1 for x in w if x in {"the", "and", "of", "to", "is", "in", "that", "it", "for", "with"})
    if id_hits == en_hits == 0:
        return "lain"
    return "id" if id_hits >= en_hits else "en"


def _cv(values: list[int]) -> float:
    if len(values) < 2:
        return 0.0
    arr = np.asarray(values, dtype=float)
    m = arr.mean()
    return float(arr.std() / m) if m > 0 else 0.0


def _mattr(tokens: list[str], window: int = 50) -> float:
    if not tokens:
        return 0.0
    if len(tokens) <= window:
        return len(set(tokens)) / len(tokens)
    step = max(1, (len(tokens) - window) // 40)  # batasi jumlah jendela agar cepat
    vals = [len(set(tokens[i:i + window])) / window for i in range(0, len(tokens) - window + 1, step)]
    return float(np.mean(vals))


def _phrase_re(phrases: list[str]) -> re.Pattern:
    alts = "|".join(re.escape(p) for p in sorted(set(phrases), key=len, reverse=True))
    return re.compile(r"(?<!\w)(?:" + alts + r")(?!\w)")


_PHRASE_RES: dict[int, re.Pattern] = {}


def _phrase_count(lower: str, phrases: list[str]) -> int:
    rx = _PHRASE_RES.get(id(phrases))
    if rx is None:
        rx = _PHRASE_RES[id(phrases)] = _phrase_re(phrases)
    return len(rx.findall(lower))


# (kunci, label Indonesia) — URUTAN TIDAK BOLEH DIUBAH setelah model dilatih
FEATURES: list[tuple[str, str]] = [
    ("n_words_log", "Panjang teks"),
    ("avg_sent_len", "Rata-rata panjang kalimat"),
    ("sent_len_cv", "Variasi panjang kalimat (burstiness)"),
    ("avg_word_len", "Rata-rata panjang kata"),
    ("mattr", "Keragaman kosakata"),
    ("hapax_ratio", "Rasio kata yang muncul sekali"),
    ("func_word_ratio", "Rasio kata fungsi"),
    ("transition_rate", "Kata transisi formal per kalimat"),
    ("ai_phrase_rate", "Frasa khas tulisan AI"),
    ("informal_rate", "Bahasa informal / gaul"),
    ("first_person_rate", "Kata ganti orang pertama"),
    ("comma_rate", "Koma per kalimat"),
    ("excl_q_rate", "Tanda seru / tanya"),
    ("colon_semi_rate", "Titik dua / titik koma"),
    ("paren_quote_rate", "Kurung / tanda kutip"),
    ("digit_ratio", "Proporsi angka"),
    ("upper_ratio", "Proporsi huruf kapital"),
    ("list_line_ratio", "Baris berbentuk poin/daftar"),
    ("para_len_cv", "Variasi panjang paragraf"),
    ("repeat_bigram_ratio", "Pengulangan frasa"),
    ("sent_start_repeat", "Pengulangan kata awal kalimat"),
    ("emoji_rate", "Emoji"),
    ("ellipsis_rate", "Elipsis / tanda baca berulang"),
    ("lower_start_rate", "Kalimat diawali huruf kecil"),
    ("short_sent_ratio", "Kalimat sangat pendek"),
]
FEATURE_KEYS = [k for k, _ in FEATURES]
FEATURE_LABELS = dict(FEATURES)


def stylometric_features(text: str) -> dict[str, float]:
    text = normalize_text(text)
    toks = words(text)
    n = len(toks)
    sents = sentences(text) or [text]
    ns = max(1, len(sents))
    sent_lens = [len(WORD_RE.findall(s)) for s in sents]
    lower = text.lower()
    letters = [c for c in text if c.isalpha()]
    counts = Counter(toks)
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    bigrams = list(zip(toks, toks[1:]))
    bigram_counts = Counter(bigrams)
    starts = [m.group(0).lower() for m in (WORD_RE.search(s) for s in sents) if m]
    start_counts = Counter(starts)
    nonempty_lines = [ln for ln in text.split("\n") if ln.strip()]

    f = {
        "n_words_log": math.log1p(n),
        "avg_sent_len": float(np.mean(sent_lens)) if sent_lens else 0.0,
        "sent_len_cv": _cv(sent_lens),
        "avg_word_len": float(np.mean([len(t) for t in toks])) if toks else 0.0,
        "mattr": _mattr(toks),
        "hapax_ratio": (sum(1 for c in counts.values() if c == 1) / n) if n else 0.0,
        "func_word_ratio": (sum(1 for t in toks if t in FUNCTION_WORDS) / n) if n else 0.0,
        "transition_rate": _phrase_count(lower, TRANSITIONS) / ns,
        "ai_phrase_rate": _phrase_count(lower, AI_PHRASES) / ns,
        "informal_rate": (sum(1 for t in toks if t in INFORMAL) / n) if n else 0.0,
        "first_person_rate": (sum(1 for t in toks if t in FIRST_PERSON) / n) if n else 0.0,
        "comma_rate": text.count(",") / ns,
        "excl_q_rate": (text.count("!") + text.count("?")) / ns,
        "colon_semi_rate": (text.count(":") + text.count(";")) / ns,
        "paren_quote_rate": sum(text.count(c) for c in "()\"“”") / ns,
        "digit_ratio": (sum(c.isdigit() for c in text) / len(text)) if text else 0.0,
        "upper_ratio": (sum(c.isupper() for c in letters) / len(letters)) if letters else 0.0,
        "list_line_ratio": (len(LIST_LINE_RE.findall(text)) / len(nonempty_lines)) if nonempty_lines else 0.0,
        "para_len_cv": _cv([len(WORD_RE.findall(p)) for p in paras]),
        "repeat_bigram_ratio": (sum(c - 1 for c in bigram_counts.values() if c > 1) / len(bigrams)) if bigrams else 0.0,
        "sent_start_repeat": (sum(c - 1 for c in start_counts.values() if c > 1) / ns),
        "emoji_rate": len(EMOJI_RE.findall(text)) / ns,
        "ellipsis_rate": len(re.findall(r"\.{2,}|[!?]{2,}|…", text)) / ns,
        "lower_start_rate": sum(1 for s in sents if s.lstrip()[:1].islower()) / ns,
        "short_sent_ratio": sum(1 for x in sent_lens if x <= 4) / ns,
    }
    return f


def feature_matrix(texts: list[str]) -> np.ndarray:
    rows = []
    for t in texts:
        f = stylometric_features(t)
        rows.append([f[k] for k in FEATURE_KEYS])
    return np.array(rows, dtype=float).reshape(len(texts), len(FEATURE_KEYS))


HEURISTIC_WEIGHTS = {
    # kunci fitur: (bobot, nilai acuan, batas atas)
    "sent_len_cv": (-2.2, 0.45, 3.0),     # kalimat seragam -> cenderung AI
    "transition_rate": (1.6, 0.0, 1.0),
    "ai_phrase_rate": (2.5, 0.0, 1.0),
    "list_line_ratio": (0.8, 0.0, 1.0),
    "informal_rate": (-14.0, 0.0, 1.0),
    "excl_q_rate": (-1.5, 0.0, 1.0),
    "lower_start_rate": (-2.0, 0.0, 1.0),
    "emoji_rate": (-3.0, 0.0, 1.0),
    "ellipsis_rate": (-2.0, 0.0, 1.0),
    "first_person_rate": (-3.0, 0.0, 1.0),
}


def heuristic_terms(f: dict[str, float]) -> dict[str, float]:
    return {k: w * (min(f[k], cap) - ref) for k, (w, ref, cap) in HEURISTIC_WEIGHTS.items()}


def heuristic_probability(f: dict[str, float]) -> float:
    """
    Skor awal sebelum model punya cukup data latih (cold start).
    Bobot ditetapkan manual dan sengaja dibatasi 0.15–0.85 karena
    heuristik ini hanya perkiraan kasar.
    """
    z = sum(heuristic_terms(f).values())
    p = 1.0 / (1.0 + math.exp(-z))
    return float(min(0.85, max(0.15, p)))
