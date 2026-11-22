import feedparser, requests, json, os, re, time, calendar, html
from datetime import datetime
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL_ID"]
MAX_POSTS = 1
REQUIRE_IMAGE = True
FALLBACK_NO_IMAGE = True
SEEN_FILE = "seen.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OnePieceNewsBot/1.0)"}

BING = "https://www.bing.com/news/search?format=rss&setlang=en-US&q="
GN = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="
FEEDS = [
    (BING + "%22One+Piece%22+manga+chapter", True, None),
    (BING + "%22One+Piece%22+anime+episode", True, None),
    (BING + "%22One+Piece%22+spoilers+leaks", True, None),
    (BING + "%22One+Piece%22+official+visual+illustration", True, None),
    (BING + "%22One+Piece%22+volume+release", True, None),
    ("https://animecorner.me/?s=one+piece&feed=rss2", True, None),
    ("https://www.animenewsnetwork.com/all/rss.xml", True, None),
    ("https://myanimelist.net/rss/news.xml", True, None),
    (GN + "%22One+Piece%22+chapter+when:7d", True, None),
    (GN + "%22One+Piece%22+episode+when:7d", True, None),
]

BLOCK = ["live action", "live-action", "netflix", "cosplay", "giveaway",
         "deals", "on sale", "amazon", "funko", "top 10", "top 5",
         "best anime", "anime like", "ranked", "quiz", "meme"]

CATS = [
    (["spoiler", "leak", "raw scan", "raws"], "🚨 اسپویل / لیک", True),
    (["visual", "poster", "illustration", "key art", "cover art", "artwork"], "🎨 تصویر رسمی", False),
    (["chapter", "manga", "volume", "tankobon", "jump"], "📖 مانگا", False),
    (["episode", "anime", "trailer", "season", "arc", "movie"], "📺 انیمه", False),
]

BDAYS = {
    (1, 1): ["ایس"], (1, 9): ["میهاوک"], (1, 10): ["یوستاس کید"],
    (2, 2): ["ویوی"], (2, 6): ["نیکو رابین"], (3, 2): ["سنجی"],
    (3, 9): ["فرانکی", "شنکس"], (3, 20): ["سابو"], (4, 1): ["اوسوپ"],
    (4, 2): ["جینبی"], (4, 3): ["بروک"], (4, 6): ["ریش‌سفید"],
    (5, 5): ["مانکی دی. لوفی"], (7, 3): ["نامی"], (8, 8): ["باگی"],
    (9, 2): ["بوآ هنکاک"], (10, 6): ["ترافالگار لا"],
    (11, 11): ["رورونوآ زورو"], (12, 24): ["چاپر"], (12, 31): ["گل دی. راجر"],
}

# اصلاح ترجمه‌های اشتباه رایج؛ هر چی دیدی غلط ترجمه شده اینجا اضافه کن
FIX = {
    "یک تکه": "وان‌پیس",
    "وان پیس": "وان‌پیس",
}

translator = GoogleTranslator(source="auto", target="fa")


def fa(text):
    if not text:
        return ""
    try:
        out = translator.translate(text[:900]) or text
    except Exception as ex:
        print("TRANSLATE ERROR", ex)
        return text
    for a, b in FIX.items():
        out = out.replace(a, b)
    return out


def esc(s):
    return html.escape(s or "", quote=False)


def clean(h):
    return BeautifulSoup(h or "", "html.parser").get_text(" ", strip=True)


def categorize(title):
    t = title.lower()
    for i, (words, label, sp) in enumerate(CATS):
        if any(w in t for w in words):
            return label, sp, (7 if sp else i + 2)
    return "📰 خبر", False, 6


def fresh(e, days=7):
    t = e.get("published_parsed") or e.get("updated_parsed")
    return (not t) or (time.time() - calendar.timegm(t) < days * 86400)


def is_dup(k, pool):
    nums = re.findall(r"\d+", k)
    for p in pool:
        if re.findall(r"\d+", p) != nums:
            continue
        sm = SequenceMatcher(None, k, p)
        if sm.real_quick_ratio() > 0.8 and sm.quick_ratio() > 0.8 and sm.ratio() > 0.8:
            return True
    return False


def real_link(link):
    if "bing.com/news/apiclick" in link:
        q = parse_qs(urlparse(link).query)
        if q.get("url"):
            return q["url"][0]
    return link


def load(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        entries = feedparser.parse(r.content).entries
        for e in entries:
            if e.get("link"):
                e["link"] = real_link(e["link"])
        print("FEED", r.status_code, len(entries), url[:70])
        return entries
    except Exception as ex:
        print("FEED ERROR", url[:70], ex)
        return []


def get_image(e):
    for key in ("media_content", "media_thumbnail"):
        if e.get(key):
            return e[key][0].get("url")
    for enc in e.get("enclosures", []):
        if enc.get("type", "").startswith("image"):
            return enc.get("href")
    m = re.search(r'<img[^>]+src="([^"]+)"', e.get("summary", ""))
    if m:
        return m.group(1)
    if "news.google.com" not in e.link:
        try:
            r = requests.get(e.link, headers=HEADERS, timeout=10)
            tag = BeautifulSoup(r.text, "html.parser").find("meta", property="og:image")
            if tag and tag.get("content"):
                return tag["content"]
        except Exception:
            pass
    return e.get("news_image")


def source_label(e):
    return urlparse(e.link).netloc.replace("www.", "")


def news_item(e, force):
    title = e.title.strip()
    label, spoiler, prio = categorize(title)
    if force == "spoiler":
        label, spoiler, prio = CATS[0][1], True, 7
    summary = clean(e.get("summary", ""))[:350]
    if "news.google.com" in e.link or summary.lower().startswith(title.lower()[:30]):
        summary = ""
    key_t = "t:" + re.sub(r"\W+", "", title.lower())[:80]
    keys = [e.link, key_t]
    m = re.search(r"(chapter|episode|volume)\s*(\d{1,4})", title.lower())
    if m:
        keys.append("n:%s%s:%s" % (m.group(1), m.group(2), label))
    return {"raw": True, "title": title, "summary": summary, "label": label,
            "prio": prio, "spoiler": spoiler, "e": e, "keys": keys}


def build_text(it):
    e = it["e"]
    title_fa = fa(it["title"])
    summ_fa = fa(it["summary"]) if it["summary"] else ""
    if it["spoiler"]:
        t = "<tg-spoiler>" + esc(title_fa) + "</tg-spoiler>"
    else:
        t = "<b>" + esc(title_fa) + "</b>"
    text = it["label"] + "\n\n" + t + "\n"
    if summ_fa:
        if it["spoiler"]:
            text += "\n<tg-spoiler>" + esc(summ_fa) + "</tg-spoiler>\n"
        else:
            text += "\n" + esc(summ_fa) + "\n"
    text += ('\n🔗 <a href="' + html.escape(e.link) + '">' + esc(source_label(e)) + "</a>"
             + "\n\n#OnePiece #وان_پیس")
    return text


def birthday_items(now):
    out = []
    for n in BDAYS.get((now.month, now.day), []):
        out.append({
            "text": "🎂 امروز تولد <b>" + n + "</b> است!\n\n#تولد_شخصیت #OnePiece #وان_پیس",
            "prio": 0,
            "keys": ["bday:%d:%d-%d:%s" % (now.year, now.month, now.day, n)],
        })
    return out


def episode_item():
    q = ("query { Page(perPage:1) { airingSchedules(mediaId:21, notYetAired:false, sort:TIME_DESC) "
         "{ episode airingAt media { coverImage { extraLarge } } } } }")
    try:
        r = requests.post("https://graphql.anilist.co", json={"query": q}, timeout=15)
        s = r.json()["data"]["Page"]["airingSchedules"][0]
        print("ANILIST آخرین قسمت:", s["episode"])
        if time.time() - s["airingAt"] > 3 * 86400:
            return None
        ep = s["episode"]
        return {
            "text": "📺 قسمت <b>%d</b> انیمه وان‌پیس پخش شد!\n\n"
                    '🔗 <a href="https://anilist.co/anime/21">AniList</a>\n\n#OnePiece #وان_پیس' % ep,
            "prio": 1,
            "image": s["media"]["coverImage"]["extraLarge"],
            "keys": ["ep:%d" % ep],
        }
    except Exception as ex:
        print("ANILIST ERROR", ex)
        return None


def send(it, img):
    if it.get("raw"):
        it["text"] = build_text(it)
        it["raw"] = False
    base = "https://api.telegram.org/bot" + TOKEN
    if img:
        r = requests.post(base + "/sendPhoto", timeout=30, data={
            "chat_id": CHANNEL, "photo": img,
            "caption": it["text"], "parse_mode": "HTML",
            "has_spoiler": "true" if it.get("spoiler") else "false"})
        print("TELEGRAM photo:", r.status_code, r.text[:200])
        if r.ok:
            return True
    r = requests.post(base + "/sendMessage", timeout=30, data={
        "chat_id": CHANNEL, "text": it["text"], "parse_mode": "HTML",
        "link_preview_options": json.dumps({"is_disabled": True})})
    print("TELEGRAM text:", r.status_code, r.text[:200])
    return r.ok


seen = set(json.load(open(SEEN_FILE))) if os.path.exists(SEEN_FILE) else set()
now = datetime.now(ZoneInfo("Asia/Tehran"))
items = birthday_items(now)
ep = episode_item()
if ep:
    items.append(ep)

queued = set()
pool = [s for s in seen if s.startswith("t:")]
for url, need_kw, force in FEEDS:
    entries = load(url)
    st = {"irrelevant": 0, "blocked_or_old": 0, "dup_or_seen": 0, "ok": 0}
    for e in entries:
        if not e.get("title") or not e.get("link"):
            continue
        tl = e.title.lower()
        summ = clean(e.get("summary", "")).lower()
        relevant = (re.search(r"one\s?piece", tl)
                    or len(re.findall(r"one\s?piece", summ)) >= 2)
        if not relevant:
            st["irrelevant"] += 1
            continue
        if any(b in tl for b in BLOCK) or not fresh(e):
            st["blocked_or_old"] += 1
            continue
        it = news_item(e, force)
        if any(k in seen or k in queued for k in it["keys"]) or is_dup(it["keys"][1], pool):
            st["dup_or_seen"] += 1
            continue
        queued.update(it["keys"])
        pool.append(it["keys"][1])
        items.append(it)
        st["ok"] += 1
    print("STAT", url[:50], st)

items = [i for i in items if not any(k in seen for k in i["keys"])]
items.sort(key=lambda x: x["prio"])
print("تعداد خبر آماده:", len(items))

posted = 0
noimg = []
for it in items[:15]:
    if posted >= MAX_POSTS:
        break
    img = it.get("image") or (get_image(it["e"]) if it.get("e") else None)
    if REQUIRE_IMAGE and not img and it["prio"] >= 2:
        noimg.append(it)
        print("بدون عکس:", it["keys"][0][:80])
        continue
    if send(it, img):
        seen.update(it["keys"])
        posted += 1

if posted == 0 and FALLBACK_NO_IMAGE:
    for it in noimg:
        if it["spoiler"]:
            continue
        if send(it, None):
            seen.update(it["keys"])
            posted += 1
        if posted >= MAX_POSTS:
            break

json.dump(list(seen), open(SEEN_FILE, "w"))
