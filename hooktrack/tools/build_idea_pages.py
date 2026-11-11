"""Builds the nine "content ideas for <niche> creators" tool pages (app/content/tools/content-ideas-for-<niche>-creators.html).

    python tools/build_idea_pages.py

The ideas below are written by hand, not generated at request time, so these pages cost nothing to serve. Each page also carries
the AI "ideas for my account" box (see app/freeai.py). Edit the data here and run the script to change a page.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "content" / "tools"
DATE = "2026-11-11"

NICHES = {
    "comedy": {
        "name": "comedy", "title": "Comedy",
        "intro": "Comedy travels fast on short video because a joke needs no explanation, but it is also the niche where every idea has been done. What separates a comedy video that spreads is a specific, recognisable situation and a punchline in the first few seconds.",
        "ideas": [
            ("Things my group chat says versus what we mean", "Split screen, you playing both sides"),
            ("The difference between how I act at work and at home", "Two quick costume or voice changes"),
            ("POV: you are the friend who is always late", "First-person POV, one running gag"),
            ("Every group project has these four people", "You play each person with a prop"),
            ("What I say versus what my mum hears", "Subtitled misunderstanding, deadpan"),
            ("Types of people at the gym", "Quick cuts, one exaggerated trait each"),
            ("Bad advice I would give a first-time driver", "Talking to camera, straight face"),
            ("When the waiter says enjoy your meal and you say you too", "Re-enact the awkward moment"),
            ("I tried to be productive for one whole day", "Time-stamped montage that falls apart"),
            ("Customer service calls, but honest", "Phone prop, you voice both sides"),
            ("Things I pretend to understand", "List with a reaction shot for each"),
            ("My phone battery at 1 percent versus my confidence", "Dramatic mini-scene"),
            ("The five stages of finding out the shop is closed", "Five faces, one caption each"),
            ("If your pet did your job", "Pet footage with a voiceover"),
            ("What a nature documentary would say about my morning", "Calm narrator voice over ordinary footage"),
            ("Parents learning new technology", "Affectionate impression, short and exact"),
            ("How different people say they are five minutes away", "Quick sketches with a timer"),
            ("A rejected superhero origin story", "One absurd power, serious tone"),
            ("Fake motivational speech about a tiny problem", "Big speech, low-stakes topic"),
            ("Reading my old texts out loud", "Screen recording plus your reactions"),
        ],
        "works": ["A situation people instantly recognise beats an original-but-confusing one.", "Put the funniest beat early and use the rest to build on it.", "A repeatable character or format makes viewers come back for the next one."],
        "faq": [("How do I make comedy videos that are not copies of everyone else?", "Use your own specific details: your accent, your job, your family, your town. A situation only you could describe is harder to copy than a trending sound."), ("How long should a comedy skit be?", "Most work best between 10 and 30 seconds. If the joke lands in the first three seconds, you can go slightly longer, but cut every second that does not add to it."), ("Should I use trending audio for comedy?", "It can help a video be found, but the joke has to work without it. Pick a sound that fits the bit, not just one that is popular.")],
    },
    "fitness": {
        "name": "fitness", "title": "Fitness",
        "intro": "Fitness is crowded, which makes specificity everything. Videos that name a particular mistake, a real timeframe or a person's actual situation stand out from the generic workout clip, and they are easier for a viewer to try straight away.",
        "ideas": [
            ("3 mistakes ruining your squat, and how to fix each", "Side-on camera, one fix per cut"),
            ("The 10-minute workout for people with no time", "Follow-along with an on-screen timer"),
            ("Stop doing crunches if you want visible abs", "Talking head, then a better exercise"),
            ("What 100 push-ups a day did to me in 30 days", "Day 1, day 15 and day 30 clips"),
            ("A full-body workout using just a chair", "Demo each move for ten seconds"),
            ("Why you are sore for days, and what to do about it", "Simple explanation with a whiteboard or text"),
            ("What I eat in a day to stay full on a budget", "Meal-by-meal clips with prices"),
            ("Beginner gym machines nobody explains", "Walk through three machines with the key tip"),
            ("The warm-up I do before every lift", "A single flowing take, captions for each move"),
            ("How I would start over at the gym with zero experience", "Talking to camera, a three-step plan"),
            ("Home workout for people with bad knees", "Low-impact moves, explain the modification"),
            ("Walking 10,000 steps versus a 20-minute workout", "Side-by-side comparison with the real trade-offs"),
            ("5 stretches for desk workers", "One stretch per cut, hold time on screen"),
            ("Myth: lifting makes you bulky", "State the myth, show real before and after of normal training"),
            ("How I fit training around a night-shift job", "A day-in-the-life with the schedule"),
            ("Progressive overload explained in 30 seconds", "Simple numbers on screen, week one versus week four"),
            ("What I changed to finally hit a pull-up", "The progression steps, in order"),
            ("Resting heart rate: what is a good number", "Plain explanation, with a note that it is not medical advice"),
            ("The cheapest gym equipment worth buying", "Show each item and the one exercise it replaces"),
            ("Gym anxiety: how I got over it", "Honest story, three practical tips"),
        ],
        "works": ["A clear before and after, with a real timeframe, is more convincing than a promise.", "Show the exercise from the angle that reveals the mistake.", "Say who the workout is for: a beginner, a busy parent, someone with bad knees."],
        "faq": [("Do I need a gym to make fitness content?", "No. Home workouts, walking and bodyweight routines are some of the most searched fitness topics, and they are easier for viewers to copy."), ("Can I give health advice in fitness videos?", "Keep it to general information from what you know, say it is not medical advice, and avoid claims about curing or treating conditions. Qualifications matter if you give specific advice."), ("What makes a fitness hook strong?", "A specific mistake or result, such as '3 mistakes ruining your squat', tends to hold attention better than a general 'best exercises' title.")],
    },
    "beauty": {
        "name": "beauty", "title": "Beauty",
        "intro": "Beauty content works when the viewer can see the result and copy the steps. The strongest videos are specific about the product, the skin type or the occasion, and honest about what did and did not work.",
        "ideas": [
            ("The 5-minute makeup look for busy mornings", "Timer on screen, one step per cut"),
            ("Three drugstore products I would buy again", "Show each product and one honest reason"),
            ("Why your foundation looks cakey, and the fix", "Close-up comparison of the same face"),
            ("Skincare routine for oily skin, in the right order", "Numbered steps with product names"),
            ("What I stopped using on my skin and why", "Talking to camera, one product per beat"),
            ("Get ready with me for a first date", "GRWM with a voiceover of the plan"),
            ("Nail shapes explained in 20 seconds", "Show each shape on a hand"),
            ("A natural-looking makeup tutorial for beginners", "Slow, clear steps with the brush names"),
            ("How to make a lipstick last all day", "Test it through a meal, show the result"),
            ("Dupes that actually work", "Side-by-side swatches in daylight"),
            ("Hair mistakes making your hair look thinner", "Before and after of one change"),
            ("The sunscreen mistakes everyone makes", "Show the amount and the reapplication"),
            ("A full-face makeup look under 10 dollars", "Receipt on screen at the end"),
            ("How I cover dark circles without it looking heavy", "Step-by-step with a close-up result"),
            ("My honest review after a month of using this serum", "Day 1, day 14, day 30 photos in the same light"),
            ("Brush cleaning, the easy way", "Quick before and after of the water"),
            ("Eyeliner for hooded eyes", "Show the technique with the eye open and closed"),
            ("Simple wedding-guest makeup", "Occasion-based look with a quick checklist"),
            ("Things dermatologists often tell people", "Share general tips, with a note to see a professional"),
            ("What is actually in my makeup bag", "Pull items out one at a time with a one-line verdict"),
        ],
        "works": ["Show the result in daylight and in the first seconds.", "Name the product and the skin type so viewers know if it applies to them.", "Be honest about what did not work. It builds trust faster than a perfect review."],
        "faq": [("Do I need to disclose sponsored beauty videos?", "Yes. Most platforms and many countries require a clear label for paid or gifted content. Check the rules where you and your audience live."), ("How do I avoid making skincare claims I cannot back up?", "Describe what you did and what you saw, not what a product will do for everyone, and avoid medical claims about treating conditions."), ("What lighting works best for beauty videos?", "Natural daylight facing a window is the simplest option, and it shows colour more truthfully than a ring light for many looks.")],
    },
    "food": {
        "name": "food", "title": "Food",
        "intro": "Food is one of the easiest niches to start in, because viewers want recipes they can actually cook. The videos that spread are quick, show the finished dish early and give enough detail that someone could make it tonight.",
        "ideas": [
            ("A 15-minute dinner using only five ingredients", "Overhead shot, ingredients listed on screen"),
            ("The mistake that makes scrambled eggs rubbery", "Side-by-side of two pans"),
            ("Meal prep for the week on a 30 dollar budget", "Show each meal and the receipt"),
            ("What I cook when I have no energy", "Lazy but tasty options, honest portions"),
            ("Restaurant-style fried rice at home", "One pan, the key technique in close-up"),
            ("Pasta water: why you should keep it", "Show the sauce coming together"),
            ("Three sauces that make any chicken taste better", "Three quick builds, a taste at the end"),
            ("The right way to cook rice every time", "A measurement graphic and a fluffy result"),
            ("Healthy snacks I actually enjoy", "Quick assembly shots, no lecture"),
            ("Cooking a dish from my grandmother's recipe", "A story with the steps behind it"),
            ("One ingredient, three meals", "Reuse one ingredient across three dishes"),
            ("How to chop an onion without crying", "Slow motion of the technique"),
            ("Air fryer recipes that are not frozen food", "Three real recipes with timing"),
            ("Breakfast ideas that are not cereal", "Quick montage, ingredients on screen"),
            ("Eating a full day for under 10 dollars", "Meal by meal with prices"),
            ("Dishes that taste better the next day", "Make it, then reheat and taste"),
            ("Tasting the viral recipe so you do not have to", "Make it exactly as shown, give an honest verdict"),
            ("Kitchen tools worth buying and ones to skip", "Show each with a one-line reason"),
            ("How I fix food that is too salty or too spicy", "Show the rescue in the pan"),
            ("A cheap dinner for two that looks fancy", "Plating close-ups at the end"),
        ],
        "works": ["Show the finished dish in the first second, then how it is made.", "Put quantities and times on screen so it can be cooked from the video.", "Reusable ingredients and a cost on screen make a video feel practical."],
        "faq": [("Do I need a fancy kitchen to make food content?", "No. A phone, good light and a clean worktop are enough. Overhead shots near a window work well."), ("Should I include the full recipe in the video?", "Put the key amounts on screen and the full recipe in the caption or a pinned comment, so people can save it."), ("How do I handle dietary and allergy information?", "State what a recipe contains, and do not promise it is safe for an allergy or a medical diet unless you are qualified to say so.")],
    },
    "tech": {
        "name": "tech and business", "title": "Tech and Business",
        "intro": "In tech and business, viewers want a clear answer to a problem they have right now: a tool that saves time, a number that explains something, a mistake to avoid. Plain language and one concrete takeaway beat jargon.",
        "ideas": [
            ("3 free tools that save me hours every week", "Screen recording of each tool in action"),
            ("The email mistake costing you replies", "Show a before and after of one message"),
            ("How I would start a side business with 100 dollars", "A step-by-step plan, honest about the odds"),
            ("What nobody tells you about your first freelance client", "Talking head with three lessons"),
            ("Phone settings you should change today", "Screen recording, one setting per cut"),
            ("The spreadsheet trick I use for every budget", "Screen recording with the formula on screen"),
            ("Is this AI tool worth paying for", "Test it on a real task and show the result"),
            ("How to spot a scam message", "Show a real example with the warning signs"),
            ("Why your laptop is slow, and the free fix", "A short checklist with screen recording"),
            ("What I would learn first if I wanted a tech job", "A plain roadmap, no hype"),
            ("My morning workflow as a one-person business", "Screen recording of the first hour"),
            ("How to price your service without undercharging", "Show the simple calculation"),
            ("Things I wish I knew before buying a smartphone", "Compare what matters, skip the spec sheet"),
            ("How I keep my passwords safe without remembering them", "Explain a password manager in plain words"),
            ("The real cost of a business idea, step by step", "Add up each cost on screen"),
            ("Free alternatives to expensive software", "Side-by-side of two tools"),
            ("How to write a cold message that gets a reply", "Show a template with notes"),
            ("A day running a small online shop", "Behind the scenes with real numbers if you can share them"),
            ("Mistakes new managers make", "Three stories, one fix each"),
            ("How to automate a boring task in 5 minutes", "Screen recording of the setup"),
        ],
        "works": ["Lead with the problem the viewer has, not the tool you are showing.", "Use screen recording so people can follow exactly what you click.", "Show real numbers when you can, and say when an example is made up."],
        "faq": [("Can I give financial advice in business videos?", "Share your own experience and general information, avoid promising returns, and say it is not financial advice. Rules differ by country, so check what applies to you."), ("How do I make technical topics easy to follow?", "Start with the result, explain it in everyday words, and show one step at a time on screen."), ("Do affiliate links need disclosure?", "Yes. If you earn from a link, say so clearly. Platforms and many countries require it.")],
    },
    "education": {
        "name": "education", "title": "Education",
        "intro": "Education content performs when it answers a question quickly and makes the viewer feel they learned something real in under a minute. Pick one idea per video, explain it in plain words, and show it instead of only saying it.",
        "ideas": [
            ("A study method that beats rereading your notes", "Demo the method on a real page"),
            ("Why you forget what you study, and how to stop", "Simple diagram or whiteboard"),
            ("Three words people mix up, explained", "Each word with an example sentence"),
            ("How to learn a language in 15 minutes a day", "A realistic daily plan"),
            ("The history of something you use every day", "Short story with a surprising fact"),
            ("Maths shortcut that saves time in exams", "Worked example, step by step"),
            ("How to write an essay introduction in 3 steps", "Show a weak and a strong example"),
            ("Why the sky is blue, in 30 seconds", "Simple demonstration or animation"),
            ("What I wish I knew before my first exam", "Talking to camera, three lessons"),
            ("How to take notes people can actually use", "Show a messy page and a clean page"),
            ("Common grammar mistakes and the fix", "Wrong and right sentences side by side"),
            ("Science experiments you can do at home", "Safe, simple, with results shown"),
            ("How memory palaces work", "Walk through one with a short list"),
            ("A one-minute explanation of how credit works", "Plain-language example with numbers"),
            ("What to do when you cannot focus on studying", "Three practical, tested ideas"),
            ("Geography facts that sound fake but are true", "One fact per cut with a map"),
            ("How I scored higher by changing one habit", "Share the habit and the evidence"),
            ("Interview questions and how to answer them", "Show a weak and a strong answer"),
            ("Free websites to learn almost anything", "Quick tour with the one thing each is best for"),
            ("A study plan for the week before an exam", "A day-by-day schedule on screen"),
        ],
        "works": ["One idea per video, explained so a beginner can follow.", "Show a worked example or a demo instead of only describing.", "Cite where a fact comes from when it matters, and correct yourself in public if you are wrong."],
        "faq": [("How do I keep education videos from feeling like a lecture?", "Start with the surprising answer or the problem, keep sentences short, and change what is on screen every few seconds."), ("Do I need to be an expert to teach?", "You need to be accurate about what you teach and honest about what you do not know. Many good education creators teach what they recently learned."), ("What is the best length for an educational short?", "Often 30 to 60 seconds for one idea. If a topic needs more, split it into a series.")],
    },
    "gaming": {
        "name": "gaming", "title": "Gaming",
        "intro": "Gaming is full of big channels, so the way in is a specific game, a specific skill or a specific personality. Short clips that show a surprising moment, a useful trick or an honest reaction do well because they are quick to watch and easy to share.",
        "ideas": [
            ("A trick in this game most players never find", "Gameplay clip with the input shown on screen"),
            ("The settings I changed that made me better", "Walk through each setting"),
            ("Rating every weapon in the game honestly", "Quick tier list with a clip each"),
            ("My worst loss ever and what I learned", "Replay the moment with commentary"),
            ("Beginner mistakes that cost you every match", "Three clips, one lesson each"),
            ("The funniest bug I have seen", "Short clip, deadpan description"),
            ("How I got better at aiming in a week", "Practice routine and results"),
            ("Games that are better than their reviews", "Three quick recommendations with a clip each"),
            ("Is this game worth the price", "An honest verdict after real play time"),
            ("Things I wish I knew when I started this game", "List with short clips"),
            ("The best loadout for a specific map", "Show it working in a match"),
            ("What my teammates say in every match", "Impression sketch with game footage"),
            ("Speedrun tricks for beginners", "Slow the trick down and show the timing"),
            ("Game soundtracks that are better than the game", "Short clips with the music on"),
            ("Cheap gaming gear that actually works", "Show it in use, give the price"),
            ("A challenge run: beating the game with one rule", "Highlights and the moment it almost failed"),
            ("The moment I almost quit and then won", "Build tension, then the clutch"),
            ("What each rank in this game really looks like", "Compare short clips from different ranks"),
            ("How I edit my gaming clips fast", "Screen recording of your editing steps"),
            ("My favourite hidden details in this game", "Quick reveal clips with pointers on screen"),
        ],
        "works": ["Lead with the surprising moment, then explain it.", "Name the game in the first line, since viewers search by game.", "Your reaction is often the content, so keep your voice or face in the clip."],
        "faq": [("Can I use game footage without problems?", "Rules differ by publisher. Many allow gameplay footage for videos, but some restrict music or monetisation. Check each game's policy."), ("How do I stand out in a crowded gaming niche?", "Pick one game or one style and be the person who knows it best, with a personality viewers can recognise."), ("Should I use a facecam?", "It helps people connect with you, but it is not required. A strong voice and good editing can carry a video.")],
    },
    "lifestyle": {
        "name": "lifestyle and travel", "title": "Lifestyle and Travel",
        "intro": "Lifestyle and travel content works when it is personal and useful at once: your real routine with a tip someone can borrow, or a place with the practical details that a tourist page leaves out. Honest costs and honest downsides build trust.",
        "ideas": [
            ("A realistic day in my life, with the boring parts", "Time-stamped clips from morning to night"),
            ("How much my weekend trip really cost", "Add up each cost on screen"),
            ("Things I would skip in this city", "Honest list with a better alternative"),
            ("My morning routine that is actually doable", "A short routine, no 5 a.m. required"),
            ("How I plan a trip in 20 minutes", "Show the planning steps on screen"),
            ("Apartment tour on a small budget", "Walk-through with the price of each piece"),
            ("Small habits that made my life easier", "Three habits with a quick demo of each"),
            ("What I pack for a week in a carry-on", "Pack in real time with a final list"),
            ("A local's guide to eating well for cheap", "Three places with prices"),
            ("Mistakes first-time travellers make", "Story, then the fix"),
            ("Slow Sunday reset routine", "Calm montage with voiceover tips"),
            ("How I save money while living in an expensive city", "Show the real numbers"),
            ("Things that surprised me about living abroad", "Honest, specific, with a clip for each"),
            ("The best free things to do in my town", "Quick tour with one tip each"),
            ("A week of meals and costs", "A weekly summary with a total"),
            ("How I stay organised with a messy schedule", "Show the real system"),
            ("Hotel versus rental, what I would pick", "Compare costs and comfort"),
            ("Things I stopped buying", "Show each item and why"),
            ("A first day in a new country, with no plan", "Live-feel clips and one lesson"),
            ("How I make time for hobbies with a full-time job", "Show an actual weekly schedule"),
        ],
        "works": ["Include real costs and real downsides, not only the highlights.", "Give one tip someone can use today, even in a personal story.", "Be specific about place, price and time of year."],
        "faq": [("How do I avoid sharing too much of my private life?", "Decide in advance what you will not show, such as your home address, your routine in real time and other people's faces, and stick to it."), ("Do I have to travel to make travel content?", "No. Local guides, planning tips and budget breakdowns work from home, and a place you know well is a real advantage."), ("How do I handle gifted trips and stays?", "Say clearly when something was gifted or paid for, and follow the rules of the platform and of the country you are posting in.")],
    },
    "music": {
        "name": "music", "title": "Music",
        "intro": "Music creators have an advantage: the sound is the hook. The videos that travel pair a strong first few seconds with something people can react to, whether a cover, a behind-the-scenes look at writing, or a quick lesson.",
        "ideas": [
            ("A cover of a popular song in a totally different style", "Performance, with the style change in the first seconds"),
            ("How I write a chorus in five minutes", "Screen or hands-on demo of the process"),
            ("Three chords that fit hundreds of songs", "Play each and name a song"),
            ("Singing a song badly on purpose, then properly", "Comedic first take, then the real one"),
            ("The mistake that makes beginner guitarists sound bad", "Close-up of the hands, then the fix"),
            ("Making a beat from sounds around my house", "Record each sound and build the loop"),
            ("What a vocal warm-up really looks like", "A short routine you can follow along"),
            ("Learning a hard song in one day", "Time-lapse with the final take"),
            ("A mashup nobody asked for", "Two songs blended, with the surprise early"),
            ("Why this song feels so sad", "Show the notes or chords that create the feeling"),
            ("How to practise without getting bored", "Three practical routines"),
            ("Behind the scenes of recording a demo", "Raw clips with the finished result"),
            ("Guess the song from one note", "Quick game, reveal after each one"),
            ("My first song versus my latest", "Compare the two, with what changed"),
            ("Music theory in 30 seconds", "One concept with a clear example"),
            ("Busking for a day and what I earned", "Highlights and the final total"),
            ("The gear I started with and what I use now", "Show each piece and the upgrade reason"),
            ("A song I wrote about a tiny, silly thing", "Short original with the lyrics on screen"),
            ("How I overcame stage fright", "Honest story with practical tips"),
            ("Reacting to my old covers", "Clips of the old version, then your reaction"),
        ],
        "works": ["Start on the most interesting few seconds of sound, not a long intro.", "Show your hands, your face or the process, not only the audio.", "Make it easy to react to: a challenge, a mashup or an honest before and after."],
        "faq": [("Can I post covers without copyright problems?", "Rules differ by platform and country. Many platforms have licences for music in videos, but they can still restrict a video. Check each platform's rules before you rely on it."), ("How do I get my own music heard?", "Use a short, strong clip of your song in a video built around a story or a challenge, not just the song alone."), ("Do I need professional gear to start?", "No. A phone and a quiet room are enough to begin, and many viral clips are rough recordings with a strong idea.")],
    },
}


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def page(slug: str, n: dict) -> str:
    title = f"{n['title']} Content Ideas: 20 TikTok, Reels and Shorts Ideas"
    meta = {"title": title, "crumb": f"{n['title']} content ideas",
            "description": f"20 specific video ideas for {n['name']} creators on TikTok, Instagram Reels and YouTube Shorts, with how to film each, plus a free AI tool that writes ideas for your own account.",
            "date": DATE, "updated": DATE}
    rows = "\n".join(f"<tr><td>{esc(h)}</td><td>{esc(f)}</td></tr>" for h, f in n["ideas"])
    works = "\n".join(f"<li>{esc(w)}</li>" for w in n["works"])
    faq = "\n".join(f"<details><summary>{esc(q)}</summary><p>{esc(a)}</p></details>" for q, a in n["faq"])
    return f"""<!--meta {json.dumps(meta)} -->
<h1>{esc(n['title'])} content ideas: 20 video ideas for TikTok, Reels and Shorts</h1>
<p class="lead">{esc(n['intro'])} Below are 20 ideas for {esc(n['name'])} creators, each with how to film it. For ideas written for your own account, use the free AI tool.</p>

{{{{ai:ideas-{slug}}}}}

<h2>20 {esc(n['name'])} video ideas</h2>
<div class="table-wrap"><table>
<thead><tr><th>Idea, written as a hook</th><th>How to film it</th></tr></thead>
<tbody>
{rows}
</tbody></table></div>
<p class="note">These are starting points, not guarantees. Make each one specific to you, and check how it does against your own normal before you make more like it.</p>

<h2>What tends to work in {esc(n['name'])}</h2>
<ul>
{works}
</ul>

<h2>Make the first line count</h2>
<p>Any of these ideas gets stronger with a better opening line. Paste your version into the <a href="/tools/video-hook-scorer">free hook scorer</a>, or turn an idea into a full script with the <a href="/tools/video-script-generator">video script generator</a>. When the video is up, the <a href="/tools/tiktok-analytics">analytics checker</a> shows whether it beat your own normal.</p>
<p>Want to see what is working right now for creators your size? <a href="/">HookTrack</a> finds recent videos that did several times better than their own channel usually does, and tracks your own results across every account.</p>

<h2>Questions</h2>
{faq}
<details><summary>Is what I type in the AI tool stored?</summary><p>No. What you type is sent to our AI provider to write the ideas, and we do not store it. We keep only an anonymous count of uses so we can apply the daily limit.</p></details>
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for slug, n in NICHES.items():
        path = OUT / f"content-ideas-for-{slug}-creators.html"
        path.write_text(page(slug, n), encoding="utf-8")
        print("wrote", path.name, len(n["ideas"]), "ideas")


if __name__ == "__main__":
    main()
