# bot/helpers/ai.py
# Contains helper functions for interacting with the Google Gemini API.

import google.generativeai as genai
import json
from bot.config import GEMINI_API_KEY

# Configure the Gemini AI model
try:
    genai.configure(api_key=GEMINI_API_KEY)
    ai_model = genai.GenerativeModel('gemini-2.5-flash')
except Exception as e:
    print(f"Error configuring Gemini AI: {e}")
    ai_model = None

# Fallback JSON data in case the AI fails
EMBED_FALLBACKS = {
    "sotw_poll": {"title": "Skill of the Week Poll!", "description": "Cast your vote for the next Skill of the Week!", "color": 0x00ff00},
    "sotw_start": {"title": "SOTW Started!", "description": "A new Skill of the Week competition for **{skill}** has begun!", "color": 0x00ff00},
    "raffle_start": {"title": "New Raffle!", "description": "A new raffle for **{prize}** has started!", "color": 0x00ff00},
    "giveaway_start": {"title": "New Giveaway!", "description": "Enter the giveaway for a chance to win **{prize}**!", "color": 0x00ff00},
    "bingo_start": {"title": "Bingo Event Started!", "description": "A new clan bingo event has begun!", "color": 0x00ff00},
    "points_award": {"title": "Points Awarded!", "description": "You have received **{amount} Clan Points** for *{reason}*.", "color": 0x00ff00},
    "pvm_event_start": {"title": "New PVM Event: {title}!", "description": "{description}", "color": 0x00ff00},
}

async def generate_announcement_json(event_type: str, details: dict = None) -> dict:
    """
    Generates a JSON object for a Discord embed using the Gemini API.
    Provides a fallback if the API call fails.
    """
    if not ai_model:
        return EMBED_FALLBACKS.get(event_type, {}).format(**(details or {}))

    details = details or {}
    persona_prompt = """
You are TaskmasterGPT, the grandmaster of clan events for a Discord server.
Your tone is epic, engaging, and highly detailed.
Your task is to generate a JSON object for a Discord embed with "title", "description", and "color" keys (as an integer).
Use vivid language and Discord markdown. Do not use emojis.
"""
    # Specific prompts can be added here as in the original file
    specific_prompt = f"Generate an embed for an event of type '{event_type}' with details: {details}"

    full_prompt = f"{persona_prompt}\n\nRequest: {specific_prompt}\n\nJSON Output:"
    try:
        response = await ai_model.generate_content_async(full_prompt)
        clean_json_string = response.text.strip().lstrip("\`\`\`json").rstrip("\`\`\`")
        return json.loads(clean_json_string)
    except Exception as e:
        print(f"Error generating AI announcement for {event_type}: {e}")
        fallback = EMBED_FALLBACKS.get(event_type, {})
        # Format fallback with details if possible
        for key, value in fallback.items():
            if isinstance(value, str):
                fallback[key] = value.format(**details)
        return fallback

async def generate_recap_text(gains_data: list) -> str:
    """Generates a weekly recap summary using the Gemini API."""
    if not ai_model:
        return "The Taskmaster is currently reviewing the ledgers."
    # Simplified prompt for brevity
    prompt = f"Write a formal and encouraging weekly OSRS clan recap. Announce the top 3 with flair. Data:\n{gains_data[:10]}"
    try:
        response = await ai_model.generate_content_async(prompt)
        return response.text
    except Exception as e:
        print(f"Error generating AI recap: {e}")
        return "An error occurred while generating the recap."

async def generate_osrs_profile_summary(osrs_name: str, skills_data: dict) -> str:
    """Generates a brief AI summary for an OSRS profile."""
    if not ai_model:
        return f"A formidable warrior of level {skills_data.get('overall', {}).get('level', 'N/A')}."
        
    overall_level = skills_data.get('overall', {}).get('level', 'N/A')
    top_skills = sorted([(k, v['level']) for k, v in skills_data.items() if k != 'overall'], key=lambda item: item[1], reverse=True)[:3]
    top_skills_str = ", ".join([f"{s.capitalize()} (Lv{l})" for s, l in top_skills])

    prompt = f"Provide a brief, engaging OSRS character summary (1-2 sentences, no markdown/emojis). Player: {osrs_name}, Overall Level: {overall_level}, Top 3 Skills: {top_skills_str}"
    try:
        response = await ai_model.generate_content_async(prompt)
        return response.text
    except Exception:
        return f"A formidable warrior of level {overall_level}."