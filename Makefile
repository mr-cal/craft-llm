SOURCES = setup_container.py

.PHONY: format lint

format:
	ruff format $(SOURCES)
	ruff check --fix $(SOURCES)

lint:
	ruff check $(SOURCES)
	ty check $(SOURCES)
