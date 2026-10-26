"""Meeting-type templates and every prompt sent to Sarvam-105B."""
from __future__ import annotations

import json

MEETING_TYPES: dict[str, dict] = {
    "gram_sabha": {
        "label": "Gram Sabha / Panchayat",
        "context": "a Gram Sabha or Gram Panchayat meeting in rural India. Speakers usually include the Sarpanch/Pradhan, the Panchayat Secretary, ward members, government functionaries and villagers.",
        "decisions": "Resolutions passed",
        "actions": "Work assigned",
        "extras": [
            ["funds", "Money and schemes mentioned", "Every amount, budget, grant or scheme mentioned (MGNREGA, PMAY, Jal Jeevan Mission, Finance Commission grants etc.) with what it is for."],
            ["complaints", "Complaints raised by people", "Each grievance raised by a villager or member, who raised it and what reply was given."],
            ["attendance", "Attendance and quorum", "Anything said about attendance, quorum, who was present or absent."],
        ],
    },
    "parliament": {
        "label": "Lok Sabha / Rajya Sabha / Vidhan Sabha",
        "context": "a legislative proceeding (Lok Sabha, Rajya Sabha or a State Assembly). Speakers include the Chair/Speaker, Ministers and Members.",
        "decisions": "Bills, motions and outcomes",
        "actions": "Assurances given by the government",
        "extras": [
            ["questions", "Questions raised and replies", "Each question or issue raised by a member, who raised it, and the substance of the reply."],
            ["positions", "Positions taken", "The stand of each party or member on the main issue, stated neutrally and without judgement."],
            ["procedure", "Adjournments, walkouts and procedure", "Adjournments, walkouts, points of order, rulings by the Chair."],
        ],
    },
    "court": {
        "label": "Court / tribunal hearing",
        "context": "a court or tribunal hearing. Speakers include the Judge/Bench, counsel for each side, parties and court staff.",
        "decisions": "Orders and directions",
        "actions": "Compliance and next steps",
        "extras": [
            ["arguments", "Arguments by each side", "The main arguments made by each side, attributed to the side that made them."],
            ["next_date", "Next date of hearing", "The next hearing date and what must happen before it."],
        ],
        "disclaimer": "AI-generated summary of a hearing. It is not a certified order or record of the court.",
    },
    "municipal": {
        "label": "Ward / municipal committee",
        "context": "a ward committee, municipal council or resident welfare meeting.",
        "decisions": "Decisions taken",
        "actions": "Work assigned",
        "extras": [
            ["funds", "Money and projects mentioned", "Budgets, tenders and projects mentioned with amounts."],
            ["complaints", "Complaints raised", "Each complaint, who raised it and the response."],
        ],
    },
    "general": {
        "label": "Any other meeting",
        "context": "a meeting or public proceeding.",
        "decisions": "Decisions",
        "actions": "Action items",
        "extras": [],
    },
}


def _lang_line(lang_name: str) -> str:
    return (f"Write every human-readable string in {lang_name}. Keep names of people, places and schemes as spoken. "
            "Keep the JSON keys in English.")


def minutes_messages(transcript: str, meta: dict, lang_name: str, partial_note: str = "") -> list[dict]:
    mt = MEETING_TYPES.get(meta.get("meeting_type"), MEETING_TYPES["general"])
    extras_spec = {k: f"list of {{text, refs}}: {desc}" for k, _, desc in mt["extras"]}
    schema = {
        "title": "short title for the meeting",
        "date_mentioned": "date if said in the meeting, else null",
        "one_line": "one sentence: what this meeting was mostly about and what came out of it",
        "summary": "list of 4-8 {text, refs}: the most important points, in order",
        "decisions": f"list of {{text, outcome, refs}}: {mt['decisions']}. outcome = passed / rejected / deferred / ordered / agreed etc.",
        "actions": f"list of {{task, owner, deadline, refs}}: {mt['actions']}. owner and deadline null if not said",
        "topics": "list of {title, start_ref, summary}: agenda items in the order discussed",
        "speakers": "list of {id, suggested_name, role, key_points}: id is the speaker id from the transcript. suggested_name only if the name or post was actually said or is obvious, else null. key_points is 1-3 short strings",
        "extras": extras_spec,
        "open_issues": "list of {text, refs}: things raised but left unresolved",
    }
    system = (
        "You are SabhaSaar, a careful minute-taker for Indian public meetings. "
        f"This transcript is from {mt['context']} "
        "Each transcript line looks like [#12 | 00:13:45 | S2 Name] text, where #12 is the segment id. "
        "Rules: use only what is in the transcript; never invent decisions, names, numbers or dates. "
        "Every item must cite the segment ids it comes from in `refs` (a list of integers), so a listener can jump to that moment. "
        "Attribute statements to speakers by name or role when known (the Sarpanch, Kamla Devi, the Minister), otherwise say 'a speaker'. "
        "Never write transcript labels like S1 or Speaker 1 in the text. Stay neutral on political content. "
        + _lang_line(lang_name)
        + " Reply with one JSON object only."
    )
    user = (
        f"Meeting: {meta.get('title') or 'untitled'} | Body: {meta.get('body') or 'unknown'} | Date: {meta.get('date') or 'unknown'}\n"
        f"{partial_note}"
        f"Return JSON with these keys:\n{json.dumps(schema, ensure_ascii=False, indent=1)}\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def merge_minutes_messages(partials: list[dict], meta: dict, lang_name: str) -> list[dict]:
    system = (
        "You merge partial minutes of one long meeting (each covering a consecutive part) into one set of minutes. "
        "Keep the same JSON keys and keep all refs exactly as given. Remove duplicates, keep order. "
        + _lang_line(lang_name) + " Reply with one JSON object only."
    )
    user = f"Meeting: {meta.get('title')}\nPARTIAL MINUTES IN ORDER:\n" + "\n\n".join(
        json.dumps(p, ensure_ascii=False) for p in partials
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def answer_messages(question: str, context: str, scope_note: str, history: list[dict], lang_hint: str | None,
                    meeting_type: str) -> list[dict]:
    mt = MEETING_TYPES.get(meeting_type, MEETING_TYPES["general"])
    lang_rule = (f"Write the answer and follow-ups only in {lang_hint}, in its own script." if lang_hint else
                 "The language of the QUESTION alone decides the answer language. Ignore the language of the records "
                 "and of earlier questions. A question in English words gets an English answer. Hindi written in "
                 "Latin letters (Hinglish) gets a Hinglish answer. A question in a native script gets an answer in that script.")
    system = (
        "You are SabhaSaar. You help people who could not attend a meeting understand what happened in it. "
        f"The records are from {mt['context']} {scope_note}"
        "Answer only from the records given. If the meeting did not discuss it, say so plainly and set status to not_discussed. "
        "Be short and concrete: who said it (by name or role, never labels like S1), what was decided, numbers and dates as stated. "
        "Speak like you are explaining to a neighbour, in simple words, 2-5 sentences. "
        "Cite the segment refs you used exactly as they appear in the records (for example \"#14\" or \"M2#14\"). "
        "Stay neutral on political content and do not give legal advice. "
        f"{lang_rule} Reply with JSON only: "
        '{"answer": str, "refs": [str], "status": "answered" | "not_discussed" | "unclear", '
        '"language_code": BCP-47 code of the answer language such as hi-IN or ta-IN, '
        '"follow_ups": [2 short follow-up questions in the same language]}'
    )
    # One plain system + user turn. An earlier version pre-filled a fake assistant reply, which made
    # sarvam-105b in JSON mode return empty answers.
    earlier = ""
    if history:
        earlier = "EARLIER QUESTIONS IN THIS CONVERSATION:\n" + "\n".join(
            f"Q: {t['question']}\nA: {t['answer']}" for t in history[-3:]) + "\n\n"
    user = f"RECORDS:\n{context}\n\n{earlier}QUESTION: {question}\n\nReply with the JSON object only."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def pick_refs_messages(question: str, minutes: dict) -> list[dict]:
    system = ("Given the minutes of a long meeting (items carry segment refs), pick the refs most likely to contain the answer "
              "to the question, in any language. Reply JSON only: {\"refs\": [int, ...]} with at most 12 refs.")
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"QUESTION: {question}\n\nMINUTES:\n{json.dumps(minutes, ensure_ascii=False)}"}]


def brief_messages(minutes: dict, meta: dict, lang_name: str) -> list[dict]:
    mt = MEETING_TYPES.get(meta.get("meeting_type"), MEETING_TYPES["general"])
    system = (
        f"Write a spoken briefing in {lang_name} for someone who missed {mt['context']} "
        "It will be read aloud by a text-to-speech voice, so: plain sentences only, no lists, no symbols, no markdown, "
        "no segment numbers, write amounts the way people say them. About 150 to 200 words, around one minute. "
        "Start with what the meeting was, then what was decided, then what happens next and who is responsible, "
        "then anything left unresolved. Stay neutral. Output only the script."
    )
    user = (f"Meeting: {meta.get('title')} ({meta.get('body') or ''}, {meta.get('date') or ''})\n"
            f"MINUTES (these may be in another language):\n{json.dumps(minutes, ensure_ascii=False)}\n\n"
            f"Now write the spoken briefing entirely in {lang_name}, using {lang_name} script."
            + ("" if lang_name == "English" else " Do not write it in English."))
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def translate_messages(text: str, lang_name: str) -> list[dict]:
    return [{"role": "system", "content": f"Translate the user's text into {lang_name}, written in {lang_name} script. "
             "Keep names, numbers and dates. Keep it natural and simple, the way people speak. Output only the translation."},
            {"role": "user", "content": text}]
