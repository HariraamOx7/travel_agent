"""Phase 0: verify each API works alone before writing any agent code."""
import os, requests
from dotenv import load_dotenv
from google import genai

load_dotenv()

def test_gemini():
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    r = client.models.generate_content(model="gemini-2.5-flash", contents="Say OK.")
    print("Gemini:", r.text)
    print("Models visible to your key:")
    for m in client.models.list():
        print("  ", m.name)   # reconfirm exact model name before Phase 0 sign-off

def test_places():
    r = requests.post(
        "https://places.googleapis.com/v1/places:searchText",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": os.environ["GOOGLE_PLACES_API_KEY"],
            "X-Goog-FieldMask": "places.displayName,places.rating,places.userRatingCount",
        },
        json={"textQuery": "hill stations near Manali"},
        timeout=10,
    )
    r.raise_for_status()
    print("Places:", [p["displayName"]["text"] for p in r.json().get("places", [])[:5]])

def test_open_meteo():
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": 32.24, "longitude": 77.19,
                "daily": "temperature_2m_max,precipitation_sum", "forecast_days": 7},
        timeout=10,
    )
    r.raise_for_status()
    print("Open-Meteo OK:", r.json()["daily"]["time"][:3])

def test_ortools():
    from ortools.sat.python import cp_model
    m = cp_model.CpModel()
    x = m.NewIntVar(0, 10, "x")
    m.Add(x == 5)
    s = cp_model.CpSolver()
    assert s.Solve(m) == cp_model.OPTIMAL
    print("OR-Tools OK, x =", s.Value(x))

if __name__ == "__main__":
    for t in (test_gemini, test_places, test_open_meteo, test_ortools):
        print(f"\n--- {t.__name__} ---")
        t()