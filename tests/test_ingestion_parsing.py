"""Parsing and normalisation in the ingestion pipeline (no network)."""

from app.services.book_ingestion import (
    BookIngestionService,
    _clean_text,
    _extract_description,
)


class TestTextCleaning:
    def test_strips_open_library_source_credit(self):
        raw = "A great story. ([source][1])"
        assert _clean_text(raw) == "A great story."

    def test_strips_back_cover_marker(self):
        raw = "The real blurb here. --back cover text that follows"
        assert _clean_text(raw) == "The real blurb here."

    def test_collapses_whitespace(self):
        assert _clean_text("too    many\n\nspaces") == "too many spaces"

    def test_empty_becomes_none(self):
        assert _clean_text("") is None
        assert _clean_text(None) is None
        assert _clean_text("   ") is None


class TestDescriptionExtraction:
    def test_plain_string(self):
        assert _extract_description("A description") == "A description"

    def test_typed_object(self):
        """Open Library returns either a string or {type, value} for the same
        field — handling only one shape silently loses half the descriptions."""
        payload = {"type": "/type/text", "value": "A description"}
        assert _extract_description(payload) == "A description"

    def test_unexpected_shape_returns_none(self):
        assert _extract_description(["a", "list"]) is None
        assert _extract_description(None) is None


class TestOpenLibraryParsing:
    def setup_method(self):
        self.service = BookIngestionService.__new__(BookIngestionService)

    def test_parses_a_full_work(self):
        work = {
            "key": "/works/OL123W",
            "title": "The Hobbit",
            "authors": [{"name": "J.R.R. Tolkien"}],
            "cover_id": 8231856,
            "first_publish_year": 1937,
            "subject": ["Fantasy fiction", "Adventure", "Accessible book"],
            "description": {"type": "/type/text", "value": "A hobbit's journey."},
        }
        parsed = self.service._parse_open_library_work(work, "fantasy")

        assert parsed["title"] == "The Hobbit"
        assert parsed["author"] == "J.R.R. Tolkien"
        assert parsed["publication_year"] == 1937
        assert parsed["description"] == "A hobbit's journey."
        assert "8231856" in parsed["cover_url"]
        assert "fantasy" in parsed["genres"]
        # Noise subject filtered out.
        assert "accessible book" not in [g.lower() for g in parsed["genres"]]

    def test_missing_title_is_rejected(self):
        assert self.service._parse_open_library_work({"key": "/works/X"}, "fiction") is None

    def test_missing_author_defaults(self):
        parsed = self.service._parse_open_library_work(
            {"key": "/works/X", "title": "Anonymous Work"}, "fiction"
        )
        assert parsed["author"] == "Unknown"

    def test_falls_back_to_requested_genre(self):
        """Subject-free works still need a genre or they're invisible to
        genre-filtered queries."""
        parsed = self.service._parse_open_library_work(
            {"key": "/works/X", "title": "Bare Record"}, "mystery"
        )
        assert parsed["genres"] == ["mystery"]

    def test_no_cover_id(self):
        parsed = self.service._parse_open_library_work(
            {"key": "/works/X", "title": "No Cover"}, "fiction"
        )
        assert parsed["cover_url"] is None

    def test_multiple_authors_joined(self):
        parsed = self.service._parse_open_library_work(
            {"key": "/w/X", "title": "T", "authors": [{"name": "A"}, {"name": "B"}]},
            "fiction",
        )
        assert parsed["author"] == "A, B"


class TestGoogleBooksParsing:
    def setup_method(self):
        self.service = BookIngestionService.__new__(BookIngestionService)

    def test_parses_a_volume(self):
        volume = {
            "id": "gb123",
            "volumeInfo": {
                "title": "Dune",
                "authors": ["Frank Herbert"],
                "description": "Desert planet politics.",
                "publishedDate": "1965-08-01",
                "pageCount": 412,
                "categories": ["Fiction / Science Fiction"],
                "averageRating": 4.5,
                "ratingsCount": 1200,
                "industryIdentifiers": [
                    {"type": "ISBN_13", "identifier": "9780441013593"},
                    {"type": "ISBN_10", "identifier": "0441013597"},
                ],
                "imageLinks": {"thumbnail": "http://books.google.com/x.jpg"},
            },
        }
        parsed = self.service._parse_google_volume(volume)

        assert parsed["title"] == "Dune"
        assert parsed["page_count"] == 412
        assert parsed["publication_year"] == 1965
        assert parsed["isbn"] == "9780441013593"
        assert parsed["source_ids"]["google_books_id"] == "gb123"
        assert "science_fiction" in parsed["genres"]
        # http thumbnails are upgraded — mixed content breaks on HTTPS clients.
        assert parsed["cover_url"].startswith("https://")

    def test_year_only_date(self):
        parsed = self.service._parse_google_volume(
            {"id": "x", "volumeInfo": {"title": "T", "publishedDate": "1999"}}
        )
        assert parsed["publication_year"] == 1999

    def test_unparseable_date_is_none(self):
        parsed = self.service._parse_google_volume(
            {"id": "x", "volumeInfo": {"title": "T", "publishedDate": "unknown"}}
        )
        assert parsed["publication_year"] is None

    def test_isbn10_used_when_no_isbn13(self):
        parsed = self.service._parse_google_volume(
            {
                "id": "x",
                "volumeInfo": {
                    "title": "T",
                    "industryIdentifiers": [{"type": "ISBN_10", "identifier": "0441013597"}],
                },
            }
        )
        assert parsed["isbn"] == "0441013597"

    def test_missing_title_rejected(self):
        assert self.service._parse_google_volume({"id": "x", "volumeInfo": {}}) is None

    def test_minimal_volume_does_not_crash(self):
        parsed = self.service._parse_google_volume(
            {"id": "x", "volumeInfo": {"title": "Bare"}}
        )
        assert parsed["title"] == "Bare"
        assert parsed["author"] == "Unknown"
        assert parsed["page_count"] is None
