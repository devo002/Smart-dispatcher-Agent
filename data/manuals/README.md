# Manuals corpus

Drop real PDF inverter / heat-pump manuals here. The ingestion pipeline
(`src/enpal_dispatcher/ingest/build_index.py`) will:

1. Walk every `*.pdf` in this folder
2. Extract text with `pypdf`
3. Chunk it (default ~600 tokens with 80 overlap)
4. Embed with `sentence-transformers/all-MiniLM-L6-v2`
5. Persist to ChromaDB at `data/chroma/`

Suggested seed PDFs (download manually — these are vendor-public):

- Huawei SUN2000 user manual (search: "SUN2000-3KTL-10KTL user manual pdf")
- SMA Sunny Boy quickstart (search: "Sunny Boy 3.0-5.0 AV-41 install guide pdf")
- Vaillant aroTHERM service guide
- Viessmann Vitocal 200-S installer manual

The agent will also search `data/known_issues.md` (already in the corpus), so the
demo works even before you add real PDFs.

> Files in this folder are gitignored by default to avoid committing large/copyrighted
> binaries.
