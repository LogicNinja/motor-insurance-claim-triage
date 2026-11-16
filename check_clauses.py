
from ingestion.parse import parse_pdf

pages = parse_pdf(
    "corpus/raw/private-car-package-policy-wording.pdf"
)

terms = [
    "SECTION I A. LOSS OF OR DAMAGE TO THE VEHICLE",
    "VI. By accidental external means",
    "GENERAL EXCEPTIONS",
    "III.",
    "LIMITATIONS AS TO USE",
    "CHAPTER 4",
]

for term in terms:
    print("\n" + "=" * 70)
    print("SEARCH:", term)

    hits = []

    for page in pages:
        position = page["text"].upper().find(term.upper())

        if position >= 0:
            context = page["text"][
                max(0, position - 180):position + 650
            ]

            hits.append((page["page"], context))

    if not hits:
        print("NOT FOUND")
        continue

    for page_number, context in hits[:5]:
        print(f"\nPAGE {page_number}:")
        print(context)
