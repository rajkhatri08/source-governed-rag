"""Evaluation set v2: pre-registered questions and labels.

Frozen once the baseline is committed (P0.1). Never edit a question or a label to improve
a metric; new questions go in a new, separately reported group.

Labels
- answerable: the corpus contains the answer.
- expected_sources: (document, heading) pairs whose text makes a claim about this exact
  question. A chunk that passed the relevance filter matches a pair when it comes from that
  document and contains that heading.
- expected_facts: lists of alternatives. Every list must match, and any one alternative in a
  list counts (case-insensitive substring). The facts are the answer in force, never a draft's.
- expected_tier: the tier rules applied to the expected sources' governance state in the eval
  database: GDPR approved and current; retention policy approved but past review; draft AI
  policy unapproved. An out-of-corpus question expects a refusal, which is BRONZE.
- expected_conflict (conflict group only, scored from P2.4): the topic, the regulatory floor,
  and each policy's verdict against it.

v1-01 to v1-23 are the questions of eval_questions.py, copied verbatim.
"""

GDPR_12 = ("gdpr_ch3.txt", "Art. 12 GDPR")
GDPR_15 = ("gdpr_ch3.txt", "Art. 15 GDPR")
GDPR_17 = ("gdpr_ch3.txt", "Art. 17 GDPR")
GDPR_33 = ("gdpr_ch3.txt", "Art. 33 GDPR")
RETENTION_S1 = ("retention_policy_v1.txt", "Section 1.")
RETENTION_S2 = ("retention_policy_v1.txt", "Section 2.")
RETENTION_S3 = ("retention_policy_v1.txt", "Section 3.")
DRAFT_S1 = ("draft_ai_policy.txt", "Section 1.")
DRAFT_S2 = ("draft_ai_policy.txt", "Section 2.")
DRAFT_S3 = ("draft_ai_policy.txt", "Section 3.")

BREACH_DEADLINE_SOURCES = [GDPR_33, RETENTION_S2, DRAFT_S3]
ACCESS_DEADLINE_SOURCES = [GDPR_12, RETENTION_S3]
AUTOMATED_DEADLINE_SOURCES = [GDPR_12, DRAFT_S2]

HOURS_72 = ["72 hours", "72-hour", "seventy-two hours"]
ONE_MONTH = ["one month", "1 month", "a month"]
FREE_OF_CHARGE = ["free of charge", "no charge", "without charge", "no cost"]
ERASURE = ["erasure", "erase", "right to be forgotten"]
ERASURE_REFUSAL_GROUNDS = ["freedom of expression", "legal obligation", "public interest",
                           "public health", "archiving", "legal claims",
                           "manifestly unfounded", "excessive"]
COMPLAINT = ["supervisory authority", "complaint"]

BREACH_CONFLICT = {
    "topic": "breach_notification",
    "floor": "72 hours",
    "verdicts": {"retention_policy_v1.txt": "gap", "draft_ai_policy.txt": "not_in_force"},
}
ACCESS_CONFLICT = {
    "topic": "data_subject_request",
    "floor": "one month",
    "verdicts": {"retention_policy_v1.txt": "gap"},
}
AUTOMATED_CONFLICT = {
    "topic": "data_subject_request",
    "floor": "one month",
    "verdicts": {"draft_ai_policy.txt": "not_in_force"},
}

QUESTIONS = [
    # --- v1-answerable: the answer is in Articles 12, 15, 17 or 33 ---
    {"id": "v1-01", "group": "v1-answerable",
     "question": "how long does a controller have to notify a breach",
     "answerable": True, "expected_sources": BREACH_DEADLINE_SOURCES,
     "expected_facts": [HOURS_72], "expected_tier": "BRONZE"},
    {"id": "v1-02", "group": "v1-answerable",
     "question": "can I ask a company to delete my data",
     "answerable": True, "expected_sources": [GDPR_17],
     "expected_facts": [ERASURE], "expected_tier": "GOLD"},
    {"id": "v1-03", "group": "v1-answerable",
     "question": "what information can I request about my own data",
     "answerable": True, "expected_sources": [GDPR_15],
     "expected_facts": [["purpose"], ["recipient"]], "expected_tier": "GOLD"},
    {"id": "v1-04", "group": "v1-answerable",
     "question": "do companies have to provide the information for free",
     "answerable": True, "expected_sources": [GDPR_12],
     "expected_facts": [FREE_OF_CHARGE], "expected_tier": "GOLD"},
    {"id": "v1-05", "group": "v1-answerable",
     "question": "what happens if a company ignores my data request",
     "answerable": True, "expected_sources": [GDPR_12],
     "expected_facts": [COMPLAINT], "expected_tier": "GOLD"},
    {"id": "v1-06", "group": "v1-answerable",
     "question": "when can a company refuse my right to be forgotten",
     "answerable": True, "expected_sources": [GDPR_17, GDPR_12],
     "expected_facts": [ERASURE_REFUSAL_GROUNDS], "expected_tier": "GOLD"},
    {"id": "v1-07", "group": "v1-answerable",
     "question": "how quickly does a company need to respond to an access request",
     "answerable": True, "expected_sources": ACCESS_DEADLINE_SOURCES,
     "expected_facts": [ONE_MONTH], "expected_tier": "SILVER"},
    {"id": "v1-08", "group": "v1-answerable",
     "question": "do I have the right to know who my data was shared with",
     "answerable": True, "expected_sources": [GDPR_15],
     "expected_facts": [["recipient"]], "expected_tier": "GOLD"},
    {"id": "v1-09", "group": "v1-answerable",
     "question": "can my data be kept if it is needed for legal claims",
     "answerable": True, "expected_sources": [GDPR_17],
     "expected_facts": [["defence of legal claims", "defense of legal claims",
                         "defend legal claims", "exercise or defence"]],
     "expected_tier": "GOLD"},
    {"id": "v1-10", "group": "v1-answerable",
     "question": "what details must be included when reporting a leak to authorities",
     "answerable": True, "expected_sources": [GDPR_33],
     "expected_facts": [["nature"], ["consequence"], ["measure"]], "expected_tier": "GOLD"},

    # --- v1-out_of_corpus: real GDPR, but not in this corpus ---
    {"id": "v1-11", "group": "v1-out_of_corpus",
     "question": "what is the maximum fine under GDPR",
     "answerable": False, "expected_sources": [], "expected_facts": [], "expected_tier": "BRONZE"},
    {"id": "v1-12", "group": "v1-out_of_corpus",
     "question": "what are the rules for transferring data outside the EU",
     "answerable": False, "expected_sources": [], "expected_facts": [], "expected_tier": "BRONZE"},
    {"id": "v1-13", "group": "v1-out_of_corpus",
     "question": "who must appoint a data protection officer",
     "answerable": False, "expected_sources": [], "expected_facts": [], "expected_tier": "BRONZE"},
    {"id": "v1-14", "group": "v1-out_of_corpus",
     "question": "what is the minimum age for a child to consent to data processing",
     "answerable": False, "expected_sources": [], "expected_facts": [], "expected_tier": "BRONZE"},
    {"id": "v1-15", "group": "v1-out_of_corpus",
     "question": "are small businesses exempt from keeping processing records",
     "answerable": False, "expected_sources": [], "expected_facts": [], "expected_tier": "BRONZE"},
    {"id": "v1-16", "group": "v1-out_of_corpus",
     "question": "how do I perform a Data Protection Impact Assessment",
     "answerable": False, "expected_sources": [], "expected_facts": [], "expected_tier": "BRONZE"},
    {"id": "v1-17", "group": "v1-out_of_corpus",
     "question": "can a company process my data based on legitimate interest",
     "answerable": False, "expected_sources": [], "expected_facts": [], "expected_tier": "BRONZE"},
    {"id": "v1-18", "group": "v1-out_of_corpus",
     "question": "who is the lead supervisory authority for a multinational business",
     "answerable": False, "expected_sources": [], "expected_facts": [], "expected_tier": "BRONZE"},
    {"id": "v1-19", "group": "v1-out_of_corpus",
     "question": "are biometric fingerprints considered special category data",
     "answerable": False, "expected_sources": [], "expected_facts": [], "expected_tier": "BRONZE"},
    {"id": "v1-20", "group": "v1-out_of_corpus",
     "question": "what are the strict rules for using website tracking cookies",
     "answerable": False, "expected_sources": [], "expected_facts": [], "expected_tier": "BRONZE"},

    # --- v1-conflicted: multiple sources disagree, or the only other source is unapproved ---
    {"id": "v1-21", "group": "v1-conflicted",
     "question": "how long do we have to report a data breach",
     "answerable": True, "expected_sources": BREACH_DEADLINE_SOURCES,
     "expected_facts": [HOURS_72], "expected_tier": "BRONZE"},
    {"id": "v1-22", "group": "v1-conflicted",
     "question": "how long do we have to respond to an access request",
     "answerable": True, "expected_sources": ACCESS_DEADLINE_SOURCES,
     "expected_facts": [ONE_MONTH], "expected_tier": "SILVER"},
    {"id": "v1-23", "group": "v1-conflicted",
     "question": "how quickly must AI-related data requests be answered",
     "answerable": True, "expected_sources": AUTOMATED_DEADLINE_SOURCES,
     "expected_facts": [ONE_MONTH], "expected_tier": "BRONZE"},

    # --- paraphrase: short, casual versions of answerable questions ---
    {"id": "v2-paraphrase-01", "group": "paraphrase",
     "question": "will my data be shared?",
     "answerable": True, "expected_sources": [GDPR_15],
     "expected_facts": [["recipient"]], "expected_tier": "GOLD"},
    {"id": "v2-paraphrase-02", "group": "paraphrase",
     "question": "how fast do I have to report a leak",
     "answerable": True, "expected_sources": BREACH_DEADLINE_SOURCES,
     "expected_facts": [HOURS_72], "expected_tier": "BRONZE"},
    {"id": "v2-paraphrase-03", "group": "paraphrase",
     "question": "can I get my data deleted",
     "answerable": True, "expected_sources": [GDPR_17],
     "expected_facts": [ERASURE], "expected_tier": "GOLD"},
    {"id": "v2-paraphrase-04", "group": "paraphrase",
     "question": "is asking for my data free",
     "answerable": True, "expected_sources": [GDPR_12],
     "expected_facts": [FREE_OF_CHARGE], "expected_tier": "GOLD"},
    {"id": "v2-paraphrase-05", "group": "paraphrase",
     "question": "what can I find out about my data",
     "answerable": True, "expected_sources": [GDPR_15],
     "expected_facts": [["purpose"], ["recipient"]], "expected_tier": "GOLD"},
    {"id": "v2-paraphrase-06", "group": "paraphrase",
     "question": "how long do they have to send me my data",
     "answerable": True, "expected_sources": ACCESS_DEADLINE_SOURCES,
     "expected_facts": [ONE_MONTH], "expected_tier": "SILVER"},
    {"id": "v2-paraphrase-07", "group": "paraphrase",
     "question": "can they say no to deleting my stuff",
     "answerable": True, "expected_sources": [GDPR_17, GDPR_12],
     "expected_facts": [ERASURE_REFUSAL_GROUNDS], "expected_tier": "GOLD"},
    {"id": "v2-paraphrase-08", "group": "paraphrase",
     "question": "what if they ignore my request",
     "answerable": True, "expected_sources": [GDPR_12],
     "expected_facts": [COMPLAINT], "expected_tier": "GOLD"},

    # --- governance: one source each, one per governance state ---
    {"id": "v2-governance-01", "group": "governance",
     "question": "how long must customer transaction records be kept?",
     "answerable": True, "expected_sources": [RETENTION_S1],
     "expected_facts": [["three years", "3 years"]], "expected_tier": "SILVER"},
    {"id": "v2-governance-02", "group": "governance",
     "question": "what does our AI usage policy apply to?",
     "answerable": True, "expected_sources": [DRAFT_S1],
     "expected_facts": [["artificial intelligence", "ai system"], ["personal data"]],
     "expected_tier": "BRONZE"},
    {"id": "v2-governance-03", "group": "governance",
     "question": "does a company have to use plain language when it tells me about my rights?",
     "answerable": True, "expected_sources": [GDPR_12],
     "expected_facts": [["concise", "intelligible", "easily accessible", "transparent",
                         "clear and plain"]],
     "expected_tier": "GOLD"},

    # --- conflict: two phrasings each for three deadlines where the sources disagree ---
    {"id": "v2-conflict-01", "group": "conflict",
     "question": "what is our deadline for reporting a personal data breach?",
     "answerable": True, "expected_sources": BREACH_DEADLINE_SOURCES,
     "expected_facts": [HOURS_72], "expected_tier": "BRONZE",
     "expected_conflict": BREACH_CONFLICT},
    {"id": "v2-conflict-02", "group": "conflict",
     "question": "we've had a data breach, how quickly must it be notified?",
     "answerable": True, "expected_sources": BREACH_DEADLINE_SOURCES,
     "expected_facts": [HOURS_72], "expected_tier": "BRONZE",
     "expected_conflict": BREACH_CONFLICT},
    {"id": "v2-conflict-03", "group": "conflict",
     "question": "what is the deadline for answering a subject access request?",
     "answerable": True, "expected_sources": ACCESS_DEADLINE_SOURCES,
     "expected_facts": [ONE_MONTH], "expected_tier": "SILVER",
     "expected_conflict": ACCESS_CONFLICT},
    {"id": "v2-conflict-04", "group": "conflict",
     "question": "how many days do we have to process a request for someone's personal data?",
     "answerable": True, "expected_sources": ACCESS_DEADLINE_SOURCES,
     "expected_facts": [ONE_MONTH], "expected_tier": "SILVER",
     "expected_conflict": ACCESS_CONFLICT},
    {"id": "v2-conflict-05", "group": "conflict",
     "question": "how quickly must we answer a request about automated decision-making?",
     "answerable": True, "expected_sources": AUTOMATED_DEADLINE_SOURCES,
     "expected_facts": [ONE_MONTH], "expected_tier": "BRONZE",
     "expected_conflict": AUTOMATED_CONFLICT},
    {"id": "v2-conflict-06", "group": "conflict",
     "question": "someone is challenging a decision our algorithm made about them, "
                 "how long do we have to respond?",
     "answerable": True, "expected_sources": AUTOMATED_DEADLINE_SOURCES,
     "expected_facts": [ONE_MONTH], "expected_tier": "BRONZE",
     "expected_conflict": AUTOMATED_CONFLICT},

    # --- no_conflict_expected: GDPR-only topics no policy speaks to (false alarms, from P2.4) ---
    {"id": "v2-no_conflict_expected-01", "group": "no_conflict_expected",
     "question": "can a company refuse a request that is manifestly unfounded or excessive?",
     "answerable": True, "expected_sources": [GDPR_12],
     "expected_facts": [["fee", "refuse to act", "burden"]], "expected_tier": "GOLD"},
    {"id": "v2-no_conflict_expected-02", "group": "no_conflict_expected",
     "question": "can a company ask me for ID before dealing with my request?",
     "answerable": True, "expected_sources": [GDPR_12],
     "expected_facts": [["additional information", "reasonable doubts", "identity"]],
     "expected_tier": "GOLD"},
    {"id": "v2-no_conflict_expected-03", "group": "no_conflict_expected",
     "question": "if a company made my data public, does it have to tell others to delete it?",
     "answerable": True, "expected_sources": [GDPR_17],
     "expected_facts": [["reasonable steps", "inform other controllers", "inform controllers"]],
     "expected_tier": "GOLD"},
    {"id": "v2-no_conflict_expected-04", "group": "no_conflict_expected",
     "question": "on what grounds can I ask for my data to be erased?",
     "answerable": True, "expected_sources": [GDPR_17],
     "expected_facts": [["no longer necessary"], ["consent"]], "expected_tier": "GOLD"},

    # --- out_of_corpus_hard: plausible, close to the corpus, not answered by it ---
    {"id": "v2-out_of_corpus_hard-01", "group": "out_of_corpus_hard",
     "question": "do we have to tell affected customers about a data breach?",
     "answerable": False, "expected_sources": [], "expected_facts": [], "expected_tier": "BRONZE"},
    {"id": "v2-out_of_corpus_hard-02", "group": "out_of_corpus_hard",
     "question": "can I move my data to another provider?",
     "answerable": False, "expected_sources": [], "expected_facts": [], "expected_tier": "BRONZE"},
    {"id": "v2-out_of_corpus_hard-03", "group": "out_of_corpus_hard",
     "question": "what security measures do we need to protect personal data?",
     "answerable": False, "expected_sources": [], "expected_facts": [], "expected_tier": "BRONZE"},
    {"id": "v2-out_of_corpus_hard-04", "group": "out_of_corpus_hard",
     "question": "how long can we keep CCTV recordings?",
     "answerable": False, "expected_sources": [], "expected_facts": [], "expected_tier": "BRONZE"},
]
