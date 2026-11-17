"""Ingestion units that need no database or network: URL scope, cleaning, chunking."""

from app.ingestion.chunker import chunk_text
from app.ingestion.cleaner import clean_html
from app.ingestion.config import CrawlConfig
from app.ingestion.lang import detect_lang
from app.ingestion.urls import in_scope, normalize_url

CFG = CrawlConfig()

PAGE = """<!doctype html><html lang="ar"><head><title>كلية الهندسة - Middle East University</title>
<script>var x = "should not appear";</script><style>.a{color:red}</style></head>
<body>
<header><nav><a href="/faculty-of-engineering-programs/">البرامج</a></nav></header>
<div class="elementor-location-header">\
<a href="https://dev.meu.edu.jo/engineering-dean-message/">كلمة العميد</a> Menu text</div>
<main>
  <h1>كلية الهندسة</h1>
  <p>تضم الكلية قسم الهندسة المدنية وقسم هندسة الطاقة المتجددة.</p>
  <h2>البرامج</h2>
  <ul><li>هندسة الأنظمة الذكية</li><li>الهندسة المدنية</li></ul>
  <div class="cookie-banner">We use cookies</div>
</main>
<footer>All rights reserved</footer>
</body></html>"""


def test_normalize_url() -> None:
    assert (
        normalize_url("https://dev.meu.edu.jo/engineering-dean-message/#top", CFG)
        == "https://www.meu.edu.jo/engineering-dean-message/"
    )
    assert normalize_url("/x/?lang=ar", CFG, base="https://www.meu.edu.jo/a/") == (
        "https://www.meu.edu.jo/x/?lang=ar"
    )
    assert normalize_url("mailto:info@meu.edu.jo", CFG) is None


def test_scope_is_engineering_and_design_only() -> None:
    yes = [
        "https://www.meu.edu.jo/faculties/faculty-of-engineering/",
        "https://www.meu.edu.jo/faculties/faculty-of-engineering/?lang=ar",
        "https://www.meu.edu.jo/%D8%A7%D9%84%D9%83%D9%84%D9%8A%D8%A7%D8%AA/"
        "%D9%83%D9%84%D9%8A%D8%A9-%D8%A7%D9%84%D9%87%D9%86%D8%AF%D8%B3%D8%A9/?lang=ar",
        "https://www.meu.edu.jo/faculties/faculty-of-architecture-and-design/",
    ]
    no = [
        "https://www.meu.edu.jo/units/departments/engineering-services-department/",
        "https://www.meu.edu.jo/faculties/faculty-of-business/",
        "https://example.com/engineering/",
        "https://www.meu.edu.jo/wp-login.php?redirect=engineering",
    ]
    assert all(in_scope(u, CFG) for u in yes)
    assert not any(in_scope(u, CFG) for u in no)


def test_all_default_seeds_are_in_scope() -> None:
    for seed in CFG.seeds:
        url = normalize_url(seed, CFG)
        assert url is not None and in_scope(url, CFG), seed


def test_cleaner_drops_boilerplate_and_keeps_content() -> None:
    page = clean_html(PAGE)
    assert page.title == "كلية الهندسة - Middle East University"
    assert page.html_lang == "ar"
    assert "## كلية الهندسة" in page.text
    assert "## البرامج" in page.text
    assert "هندسة الأنظمة الذكية" in page.text
    for junk in ("should not appear", "color:red", "Menu text", "cookies", "All rights"):
        assert junk not in page.text
    # links are still collected from menus, for discovery
    assert "/faculty-of-engineering-programs/" in page.links
    assert "https://dev.meu.edu.jo/engineering-dean-message/" in page.links


def test_detect_lang() -> None:
    assert detect_lang("هندسة الأنظمة الذكية في جامعة الشرق الأوسط") == "ar"
    assert detect_lang("Intelligent Systems Engineering at MEU") == "en"


def test_chunker_respects_size_and_keeps_heading() -> None:
    para = " ".join(f"word{i}" for i in range(120))
    text = "## Programs\n\n" + "\n".join([para] * 6) + "\n\n## Labs\n\nSmall lab section text."
    chunks = chunk_text(text, max_words=300, min_words=40)
    assert len(chunks) >= 3
    assert all(len(c.split()) <= 300 * 1.2 for c in chunks)
    assert all(c.startswith("Programs\n") for c in chunks[:-1])
    assert chunks[-1].startswith("Labs\n")
    # no words lost
    total = sum(len(c.split()) for c in chunks)
    assert total >= 6 * 120


def test_chunker_splits_one_huge_paragraph() -> None:
    huge = " ".join(["كلمة"] * 1000)
    chunks = chunk_text(huge, max_words=300)
    assert len(chunks) == 4
    assert all(len(c.split()) <= 300 for c in chunks)


def test_body_with_menu_classes_is_not_dropped() -> None:
    """Regression: MEU's <body> has classes like 'mega-menu-main-navigation kl-sticky-header'."""
    html = (
        '<html><body class="mega-menu-main-navigation kl-sticky-header">'
        '<header class="site-header"><a href="/x">menu</a></header>'
        "<div><h1>Faculty of Engineering</h1><p>Established in 2009.</p></div></body></html>"
    )
    page = clean_html(html)
    assert "Established in 2009." in page.text
    assert "menu" not in page.text
