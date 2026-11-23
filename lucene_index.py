"""
lucene_index.py - PyLucene index of the joined PFAF + Wikipedia data and a searcher.

Input : data/joined/plants_wiki.tsv      (spark_join.py: PFAF fields + wiki_* columns)
Output: data/index_lucene/               (Lucene index, one document = one plant)

Run it where PyLucene is installed (the Docker image coady/pylucene, see README.md).
Importing this module starts the JVM. Without PyLucene it can still run on JPype with the
Lucene jars on LUCENE_CLASSPATH (used only to test the code outside Docker).

Field design (the reasons are in the report, section 2):
  names        TextField, StandardAnalyzer (lower-case, no stemming, no stop words)
               latin_name, common_name, wiki_common_names, wiki_title, synonyms, other_names, family
  prose        TextField, EnglishAnalyzer (stemming + stop words)
               summary, wiki_lead, edible_uses, medicinal_uses, ... , wiki_categories
  exact values StringField (one token, not analysed; lower-case in the index)
               family_key, category (many per plant), care_key (many), status,
               wiki_match, wiki_present
  numbers      IntPoint (range queries) + StoredField (display) + NumericDocValuesField (sorting)
               edibility_rating, medicinal_rating, other_uses_rating, hardiness_min,
               hardiness_max, wiki_text_len
"""

import csv
import os
import re
import time
from collections import OrderedDict

from extractor import strip_references
from query_syntax import fuzzify, parse_filters, parse_hardiness

DATA_DIR = "data"
PLANTS_WIKI_PATH = os.path.join(DATA_DIR, "joined", "plants_wiki.tsv")
INDEX_DIR = os.path.join(DATA_DIR, "index_lucene")

_VM_READY = False


def init_vm():
    """Start the JVM once: PyLucene (lucene.initVM) or, for tests, JPype + LUCENE_CLASSPATH."""
    global _VM_READY
    if _VM_READY:
        return
    try:
        import lucene
    except ImportError:
        lucene = None
    if lucene is not None:
        env = lucene.getVMEnv() if hasattr(lucene, "getVMEnv") else None
        if env is None:
            lucene.initVM(vmargs=["-Djava.awt.headless=true"])
    else:
        classpath = os.environ.get("LUCENE_CLASSPATH")
        try:
            import jpype
            import jpype.imports
        except ImportError:
            jpype = None
        if not classpath or jpype is None:
            raise ImportError("PyLucene is not installed (run inside the coady/pylucene "
                              "Docker image, see README.md)")
        if not jpype.isJVMStarted():
            jpype.startJVM("--add-modules=jdk.incubator.vector",
                           classpath=classpath.split(os.pathsep))
    _VM_READY = True


init_vm()

from java.io import File                                                    # noqa: E402
from java.lang import Float as JFloat                                       # noqa: E402
from java.util import HashMap                                               # noqa: E402
from org.apache.lucene.analysis.en import EnglishAnalyzer                   # noqa: E402
from org.apache.lucene.analysis.miscellaneous import PerFieldAnalyzerWrapper  # noqa: E402
from org.apache.lucene.analysis.standard import StandardAnalyzer            # noqa: E402
from org.apache.lucene.document import (Document, Field, IntPoint,          # noqa: E402
                                        NumericDocValuesField, StoredField,
                                        StringField, TextField)
from org.apache.lucene.index import (DirectoryReader, IndexWriter,          # noqa: E402
                                     IndexWriterConfig, Term)
from org.apache.lucene.queryparser.classic import (MultiFieldQueryParser,   # noqa: E402
                                                   QueryParser, QueryParserBase)
from org.apache.lucene.search import (BooleanClause, BooleanQuery,          # noqa: E402
                                      IndexSearcher, MatchAllDocsQuery, TermQuery)
from org.apache.lucene.store import FSDirectory                             # noqa: E402

# ---------------------------------------------------------------- field design
NAME_FIELDS = ("latin_name", "common_name", "wiki_common_names", "wiki_title", "synonyms",
               "other_names", "family")
STORED_FIELDS = ("latin_name", "common_name", "family", "wiki_title")

# field -> boost used by the multi-field query parser (weights replace the integer
# weights of the old index; BM25 saturates term frequency, a boost scales a whole field)
SEARCH_FIELDS = OrderedDict([
    ("latin_name", 5.0), ("common_name", 4.0), ("wiki_common_names", 3.0), ("wiki_title", 2.0),
    ("synonyms", 2.0), ("other_names", 1.5), ("family", 2.0), ("wiki_categories", 1.5),
    ("summary", 1.5), ("wiki_lead", 1.5),
    ("edible_uses", 1.0), ("medicinal_uses", 1.0), ("other_uses", 1.0), ("special_uses", 1.0),
    ("physical_characteristics", 1.0), ("cultivation", 1.0), ("known_hazards", 1.0),
    ("habitats", 1.0), ("care", 1.0), ("propagation", 0.7), ("found_in", 0.7),
    ("plant_habitats", 0.7), ("range", 0.7), ("weed_potential_text", 0.5),
    ("carbon_farming", 0.5),
])
PROSE_FIELDS = [name for name in SEARCH_FIELDS if name not in NAME_FIELDS]
RATING_FIELDS = ("edibility_rating", "medicinal_rating", "other_uses_rating")


def build_analyzer():
    """StandardAnalyzer for names, EnglishAnalyzer (stemming, stop words) for prose."""
    per_field = HashMap()
    standard = StandardAnalyzer()
    for name in NAME_FIELDS:
        per_field.put(name, standard)
    return PerFieldAnalyzerWrapper(EnglishAnalyzer(), per_field)


# ---------------------------------------------------------------- building
def _int_point(name, value):
    try:
        return IntPoint(name, [value])      # IntPoint(String, int...) -> pass an array
    except TypeError:
        return IntPoint(name, value)


def _add_number(doc, name, value):
    doc.add(_int_point(name, value))
    doc.add(StoredField(name, value))
    doc.add(NumericDocValuesField(name, value))


def make_document(row):
    """One Lucene document from one row of plants_wiki.tsv."""
    def get(name):
        return strip_references(row.get(name) or "").strip()

    doc = Document()
    latin = (row.get("latin_name") or "").strip()
    family = get("family")
    if family:
        doc.add(StringField("family_key", family.lower(), Field.Store.NO))

    for name in SEARCH_FIELDS:                        # all searchable text fields
        value = latin if name == "latin_name" else get(name)
        if value:
            store = Field.Store.YES if name in STORED_FIELDS else Field.Store.NO
            doc.add(TextField(name, value, store))

    for category in filter(None, (c.strip() for c in (row.get("wiki_categories") or "").split(";"))):
        doc.add(StringField("category", category.lower(), Field.Store.NO))
    for care in filter(None, (c.strip() for c in (row.get("care") or "").split(";"))):
        doc.add(StringField("care_key", care.lower(), Field.Store.NO))
    if row.get("wiki_status"):
        doc.add(StringField("status", row["wiki_status"].strip().lower(), Field.Store.YES))
    if row.get("wiki_match"):
        doc.add(StringField("wiki_match", row["wiki_match"].strip().lower(), Field.Store.YES))
    doc.add(StringField("wiki_present", "1" if row.get("wiki_title") else "0", Field.Store.NO))

    for name in RATING_FIELDS:
        value = (row.get(name) or "").strip()
        if value.isdigit():
            _add_number(doc, name, int(value))
    zones = parse_hardiness(row.get("usda_hardiness"))
    if zones:
        _add_number(doc, "hardiness_min", zones[0])
        _add_number(doc, "hardiness_max", zones[1])
    length = (row.get("wiki_text_len") or "").strip()
    if length.isdigit():
        _add_number(doc, "wiki_text_len", int(length))
    return doc


def build_index(plants_path=PLANTS_WIKI_PATH, index_dir=INDEX_DIR):
    """Rebuild the whole index; returns counts for the report."""
    started = time.time()
    csv.field_size_limit(2 ** 31 - 1)
    os.makedirs(index_dir, exist_ok=True)
    config = IndexWriterConfig(build_analyzer())
    config.setOpenMode(IndexWriterConfig.OpenMode.CREATE)
    directory = FSDirectory.open(File(index_dir).toPath())
    writer = IndexWriter(directory, config)
    documents = with_wiki = 0
    with open(plants_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            writer.addDocument(make_document(row))
            documents += 1
            with_wiki += bool(row.get("wiki_title"))
    writer.commit()
    writer.close()
    directory.close()
    size = sum(os.path.getsize(os.path.join(index_dir, n)) for n in os.listdir(index_dir))
    return {"documents": documents, "with_wiki": with_wiki, "size_mb": round(size / 1024 / 1024, 2),
            "seconds": round(time.time() - started, 2)}


# ---------------------------------------------------------------- searching
def _total(top_docs):
    value = top_docs.totalHits.value               # a field in Lucene 9, a record method in 10
    return int(value() if callable(value) else value)


def _parse_with(parser, text):
    """parser.parse(text). PyLucene (JCC): MultiFieldQueryParser has a static parse(...) that
    hides the instance method parse(String), so the instance must be passed explicitly."""
    attempts = (lambda: parser.parse(text),
                lambda: MultiFieldQueryParser.parse(parser, text),
                lambda: QueryParser.parse(parser, text),
                lambda: QueryParserBase.parse(parser, text))
    error = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:                     # wrong calling convention, try the next
            error = exc
    raise error


class Searcher:
    """Opens the index and answers queries (the 'new index' of the application)."""

    def __init__(self, index_dir=INDEX_DIR):
        self.directory = FSDirectory.open(File(index_dir).toPath())
        self.reader = DirectoryReader.open(self.directory)
        self.searcher = IndexSearcher(self.reader)
        self.analyzer = build_analyzer()
        boosts = HashMap()
        for name, boost in SEARCH_FIELDS.items():
            boosts.put(name, JFloat(boost))
        self._fields, self._boosts = list(SEARCH_FIELDS), boosts

    def close(self):
        self.reader.close()
        self.directory.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def num_docs(self):
        return self.reader.numDocs()

    # -- query building
    def _parse(self, text, operator):
        parser = MultiFieldQueryParser(self._fields, self.analyzer, self._boosts)
        if operator == "and":
            parser.setDefaultOperator(QueryParser.Operator.AND)
        try:
            return _parse_with(parser, text)
        except TypeError:
            raise
        except Exception:        # ParseException (PyLucene: lucene.JavaError): bad quote, stray ':' ...
            return _parse_with(parser, QueryParser.escape(text))

    @staticmethod
    def _filter_query(spec):
        if spec[0] == "term":
            return TermQuery(Term(spec[1], spec[2]))
        return IntPoint.newRangeQuery(spec[1], spec[2], spec[3])

    def build_query(self, text, operator="or", fuzzy=False):
        """Lucene Query for the text (free text + filters, see query_syntax.py)."""
        text, filters = parse_filters(text)
        if fuzzy:
            text = fuzzify(text)
        main = self._parse(text, operator) if text else MatchAllDocsQuery()
        if not filters:
            return main
        builder = BooleanQuery.Builder()
        builder.add(main, BooleanClause.Occur.MUST)
        for spec in filters:
            builder.add(self._filter_query(spec), BooleanClause.Occur.FILTER)
        return builder.build()

    # -- searching
    def search(self, text, k=10, operator="or", fuzzy=False):
        """dict(query, hits, results=[dict(rank, score, doc_id, latin_name, ...)])"""
        query = self.build_query(text, operator, fuzzy)
        top = self.searcher.search(query, k)
        stored = self.searcher.storedFields()
        results = []
        for rank, score_doc in enumerate(top.scoreDocs, 1):
            doc = stored.document(score_doc.doc)
            results.append({"rank": rank, "score": float(score_doc.score), "doc_id": int(score_doc.doc),
                            "latin_name": doc.get("latin_name") or "",
                            "common_name": doc.get("common_name") or "",
                            "family": doc.get("family") or "",
                            "wiki_title": doc.get("wiki_title") or "",
                            "status": doc.get("status") or ""})
        return {"query": str(query.toString()), "hits": int(self.searcher.count(query)),
                "results": results}

    def explain(self, text, doc_id, operator="or", fuzzy=False):
        """Lucene's score explanation (BM25 factors) of one document for the query."""
        return str(self.searcher.explain(self.build_query(text, operator, fuzzy), doc_id).toString())