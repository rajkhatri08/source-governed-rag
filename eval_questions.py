"""
Evaluation set for the GDPR retrieval pipeline.

Each entry is (question, should_be_answerable, source).

should_be_answerable is the ground truth: True means the answer exists in
gdpr_ch3.txt (Articles 12, 15, 17, 33). False means the question is real GDPR
but lives in an article that is not in the corpus.

source is recorded for diagnosis. When an answerable question fails, the
source tells you which article retrieval should have found.
"""

QUESTIONS = [
    # --- Answerable: the answer is in Articles 12, 15, 17 or 33 ---
    ("how long does a controller have to notify a breach",
     True, "Art 33"),

    ("can I ask a company to delete my data",
     True, "Art 17"),

    ("what information can I request about my own data",
     True, "Art 15"),

    ("do companies have to provide the information for free",
     True, "Art 12"),

    ("what happens if a company ignores my data request",
     True, "Art 12"),

    ("when can a company refuse my right to be forgotten",
     True, "Art 17"),

    ("how quickly does a company need to respond to an access request",
     True, "Art 12"),

    ("do I have the right to know who my data was shared with",
     True, "Art 15"),

    ("can my data be kept if it is needed for legal claims",
     True, "Art 17"),

    ("what details must be included when reporting a leak to authorities",
     True, "Art 33"),

    # --- Not answerable: real GDPR, but not in this corpus ---
    ("what is the maximum fine under GDPR",
     False, "Art 83"),

    ("what are the rules for transferring data outside the EU",
     False, "Chapter 5"),

    ("who must appoint a data protection officer",
     False, "Art 37"),

    ("what is the minimum age for a child to consent to data processing",
     False, "Art 8"),

    ("are small businesses exempt from keeping processing records",
     False, "Art 30"),

    ("how do I perform a Data Protection Impact Assessment",
     False, "Art 35"),

    ("can a company process my data based on legitimate interest",
     False, "Art 6"),

    ("who is the lead supervisory authority for a multinational business",
     False, "Art 56"),

    ("are biometric fingerprints considered special category data",
     False, "Art 9"),

    # Note: not GDPR at all. Expect a noticeably higher distance than the
    # others in this group - useful for telling "wrong article" apart from
    # "wrong regulation".
    ("what are the strict rules for using website tracking cookies",
     False, "ePrivacy Directive"),
    # --- Conflicted: multiple sources disagree ---
    ("how long do we have to report a data breach",
     True, "CONFLICT: Art 33 (72h) vs retention policy (30d, expired) vs draft AI policy (24h, unapproved)"),

    ("how long do we have to respond to an access request",
     True, "CONFLICT: Art 12 (1 month) vs retention policy (60 days, expired)"),

    ("how quickly must AI-related data requests be answered",
     True, "ONLY SOURCE: draft AI policy (14 days, unapproved)"),
]