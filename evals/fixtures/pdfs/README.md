# PDF test set (P0.3b)

The synthetic files are committed. The public documents are kept in `local/`, which is gitignored: each is
large or not ours to republish. Download each one from its source into `local/` under the name given; its
sha256 tells you whether you have the exact file that was measured. `python -m evals.pdf_robustness`
reads every PDF in both folders; the questions asked of each are in `evals/pdf_questions.py`.

| File | Source | What it tests |
|---|---|---|
| `scanned_policy.pdf` | Synthetic, generated 20 Nov 2026: a fictional "Example Corp" policy rendered as one image, with no text layer | A scan: the upload must be refused with a clear "no text layer" message |
| `protected_policy.pdf` | Synthetic, generated 20 Nov 2026: a fictional "Example Corp" policy, encrypted (AES-128) with the user password `demo-1234` | A password-protected PDF: the upload must be refused with a clear message |
| `local/nist_ai_rmf_1.0.pdf` | NIST, *Artificial Intelligence Risk Management Framework (AI RMF 1.0)*, NIST AI 100-1, January 2023: https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf. A US government work (public domain), kept local for its size. 1,946,127 bytes, sha256 `7576edb531d9848825814ee88e28b1795d3a84b435b4b797d3670eafdc4a89f1` | Tables (the Core's subcategories), and a running header and page-number footer on every page |
| `local/bert_two_column.pdf` | J. Devlin et al., *BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding*, arXiv:1810.04805v2: https://arxiv.org/pdf/1810.04805. Distributed under arXiv's licence, so kept local. 775,166 bytes, sha256 `5692a5514787a8c6727b4ff3b726a3385798bc68e12138d1d4af83947e2acf6e` | A two-column layout |
| `local/apple_privacy_policy.pdf` | Apple, *Privacy Policy*, updated 30 July 2025: https://www.apple.com/legal/privacy/pdfs/apple-privacy-policy-en-ww.pdf. Apple's copyright, so kept local. 245,399 bytes, sha256 `f3171fac1aac419344d9d3c3a958a18b8130aa92d8abd0290464985966cb8f59` | A company privacy policy, and the set's one-page PDF: a web page printed as a single page of about 30,000 characters |
| `local/eu_ai_act.pdf` | Regulation (EU) 2024/1689 (the AI Act), Official Journal L, 12.7.2024: https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=OJ:L_202401689. © European Union; reuse is authorised with the source acknowledged. EUR-Lex refuses automated downloads: download it in a browser. Kept local for its size. 2,583,319 bytes, sha256 `bba630444b3278e881066774002a1d7824308934f49ccfa203e65be43692f55e` | A long regulation: 144 pages; headers and footers that alternate between odd and even pages |
| `local/edps_breaches_factsheet.pdf` | European Data Protection Supervisor, *Personal Data Breaches In A Nutshell*, 2021: https://www.edps.europa.eu/system/files/2021-05/21-05-06_databreaches_factsheet_final_en_0.pdf. © European Union, 2021; reproduction is authorised with the source acknowledged. The EDPS site refuses automated downloads: download it in a browser. Kept local for its size. 3,801,357 bytes, sha256 `b02c556da41b40bfa769e016f2037de9011b60d2ce14598b92c85e3c9ddbc0ba` | A short, heavily designed factsheet: 6 pages, about 6,000 characters of text among the graphics |

No short one-page public PDF could be downloaded automatically, so the one-page case is Apple's single long page.
