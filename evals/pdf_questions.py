"""Questions for the P0.3b PDF test set: two per accepted PDF, written from its extracted text before any was asked.

Keyed by the file's name in evals/fixtures/pdfs/ or evals/fixtures/pdfs/local/. expected_facts works as in
questions_v2.py: a list of alternatives lists; every list must match the answer, and any one alternative
in a list counts (case-insensitive substring). `where` says where in the PDF the answer is. Frozen once
the first run is committed (P0.3b): a new question gets a new id, never an edited label.
"""

PDF_QUESTIONS = {
    "nist_ai_rmf_1.0.pdf": [
        {"id": "pdf-rmf-1", "question": "What are the four functions of the AI RMF Core?",
         "expected_facts": [["govern"], ["map"], ["measure"], ["manage"]],
         "where": "Part 2, the Core: prose"},
        {"id": "pdf-rmf-2",
         "question": "Who should take responsibility for decisions about the risks of developing and deploying "
                     "AI systems?",
         "expected_facts": [["executive leadership"]],
         "where": "Part 2, Table 1, GOVERN 2.3: a table row"},
    ],
    "bert_two_column.pdf": [
        {"id": "pdf-bert-1", "question": "How many layers and how many parameters does BERT-LARGE have?",
         "expected_facts": [["24"], ["340"]],
         "where": "Section 3, model architecture: two-column body"},
        {"id": "pdf-bert-2",
         "question": "When BERT's masking procedure picks a word, how often is it replaced with the [MASK] token?",
         "expected_facts": [["80%", "80 %", "80 percent"]],
         "where": "Appendix A.1, the masking procedure: two-column body"},
    ],
    "eu_ai_act.pdf": [
        {"id": "pdf-aiact-1",
         "question": "Under the EU AI Act, whose AI literacy must providers and deployers of AI systems ensure?",
         "expected_facts": [["staff"]],
         "where": "Article 4, AI literacy: the middle of the text (45%)"},
        {"id": "pdf-aiact-2",
         "question": "Within how many days must a provider report a serious incident involving a high-risk AI system?",
         "expected_facts": [["15 days"]],
         "where": "Article 73(2), reporting of serious incidents (78%)"},
    ],
    "edps_breaches_factsheet.pdf": [
        {"id": "pdf-edps-1",
         "question": "How quickly must an EU institution notify the EDPS of a personal data breach?",
         "expected_facts": [["72 hours"]],
         "where": "What to do in case of a personal data breach"},
        {"id": "pdf-edps-2", "question": "What types of personal data breach does the EDPS factsheet describe?",
         "expected_facts": [["confidentiality"], ["availability"], ["integrity"]],
         "where": "What are the types of breaches that may occur?"},
    ],
    "apple_privacy_policy.pdf": [
        {"id": "pdf-apple-1", "question": "Below what age does Apple treat a person as a child?",
         "expected_facts": [["13"]],
         "where": "Children and Minors"},
        {"id": "pdf-apple-2",
         "question": "Which Apple company controls the personal data of people in the European Economic Area?",
         "expected_facts": [["Apple Distribution International"]],
         "where": "Apple Companies Around the World"},
    ],
}
