"""
Local Strands tool: get_pokedex_entry.

Fetches structured Pokémon data directly from PokeAPI and returns it in a
format suitable for the Dexter persona to narrate. This tool complements the
remote MCP tools by adding flavor text from the pokemon-species endpoint,
which is not available through the MCP server.
"""

import json
import urllib.parse

import httpx
from strands import tool

_POKEAPI_BASE = "https://pokeapi.co/api/v2"
_TIMEOUT = 10.0


@tool
def get_pokedex_entry(pokemon: str) -> dict:
    """Fetch a complete Pokédex entry for a Pokémon from PokeAPI.

    Retrieves types, base stats, abilities, height, weight, flavor text
    descriptions from the games (in Spanish when available, otherwise
    English), generation, habitat, legendary/mythical status, and capture
    rate. Use this tool whenever the user asks about a specific Pokémon.

    Args:
        pokemon: The Pokémon name (lowercase, e.g. "pikachu") or National
                 Pokédex number as a string (e.g. "25").

    Returns:
        A dict with full Pokédex entry data, or an error status on failure.
    """
    name_or_id = urllib.parse.quote(pokemon.strip().lower(), safe="")

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            # Primary endpoint: core pokemon data
            r_poke = client.get(f"{_POKEAPI_BASE}/pokemon/{name_or_id}")
            if r_poke.status_code == 404:
                return {
                    "status": "error",
                    "content": [{"text": f"No se encontró ningún Pokémon con el nombre o ID '{pokemon}'."}],
                }
            r_poke.raise_for_status()
            poke = r_poke.json()

            # Secondary endpoint: species data (flavor text, generation, etc.)
            # Use the species URL from the pokemon response to handle alternate forms
            species_url = poke.get("species", {}).get("url", f"{_POKEAPI_BASE}/pokemon-species/{name_or_id}")
            r_species = client.get(species_url)
            r_species.raise_for_status()
            species = r_species.json()

    except httpx.HTTPStatusError as exc:
        return {
            "status": "error",
            "content": [{"text": f"Error HTTP de PokeAPI: {exc.response.status_code} para '{pokemon}'."}],
        }
    except httpx.RequestError as exc:
        return {
            "status": "error",
            "content": [{"text": f"Error de red al contactar PokeAPI: {exc}."}],
        }

    # Types (ordered by slot)
    types = [t["type"]["name"] for t in sorted(poke["types"], key=lambda x: x["slot"])]

    # Base stats
    base_stats = {s["stat"]["name"]: s["base_stat"] for s in poke["stats"]}

    # Abilities (ordered by slot)
    abilities = [
        {"name": a["ability"]["name"], "is_hidden": a["is_hidden"]}
        for a in sorted(poke["abilities"], key=lambda x: x["slot"])
    ]

    # Flavor texts: prefer Spanish, fallback to English; deduplicate; cap at 3
    def _collect_texts(lang: str) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for entry in species.get("flavor_text_entries", []):
            if entry["language"]["name"] == lang:
                # PokeAPI uses form-feeds and newlines in game text — normalize them
                text = entry["flavor_text"].replace("\f", " ").replace("\n", " ").strip()
                if text and text not in seen:
                    seen.add(text)
                    result.append(text)
                    if len(result) == 3:
                        break
        return result

    flavor_texts = _collect_texts("es") or _collect_texts("en")

    # Generation: "generation-i" → "I"
    generation_raw = species.get("generation", {}).get("name", "")
    generation = generation_raw.replace("generation-", "").upper() if generation_raw else "unknown"

    # Habitat
    habitat_obj = species.get("habitat")
    habitat = habitat_obj["name"] if habitat_obj else "unknown"

    entry = {
        "id": poke["id"],
        "name": poke["name"],
        "height_dm": poke["height"],   # decimetres (divide by 10 for metres)
        "weight_hg": poke["weight"],   # hectograms (divide by 10 for kg)
        "types": types,
        "base_stats": base_stats,
        "abilities": abilities,
        "flavor_text": flavor_texts,
        "generation": generation,
        "habitat": habitat,
        "is_legendary": species.get("is_legendary", False),
        "is_mythical": species.get("is_mythical", False),
        "capture_rate": species.get("capture_rate"),
    }

    return {
        "status": "success",
        "content": [{"text": json.dumps(entry, ensure_ascii=False)}],
    }
