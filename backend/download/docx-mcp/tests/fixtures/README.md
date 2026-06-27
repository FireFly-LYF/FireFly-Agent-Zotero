# tests/fixtures/

Real Word documents sourced externally for correctness validation.
These are the doer-checker gate — synthetic fixtures alone are insufficient.

## Original corpus documents

- `real_contract.docx` — 50-page multi-section document with TOC, heading styles, headers, footers, and page numbers. Source: https://sample-files.com/downloads/documents/docx/sample-files.com-large-document.docx
- `real_tracked_changes.docx` — Document with tracked insertions (`w:ins`) and deletions (`w:del`), plus reviewer comments. Source: https://sample-files.com/downloads/documents/docx/sample-files.com-tracked-changes.docx
- `real_hyperlinks_footnotes.docx` — TEI Publisher test document with hyperlinks in body AND footnotes, exercises the footnotes.xml.rels vs document.xml.rels ID collision edge case. Source: https://github.com/eeditiones/tei-publisher-lib/files/7334303/test.docx

## Apache POI test suite (Category 2 — open-source DOCX test corpus)

Source: https://github.com/apache/poi/tree/trunk/test-data/document

- `poi_sample.docx` — Lorem ipsum paragraphs with footnotes and endnotes (228 words)
- `poi_footnotes.docx` — Exercises footnote XML parsing
- `poi_endnotes.docx` — Exercises endnote XML parsing (173 words)
- `poi_header_footer.docx` — Header and footer content
- `poi_diff_header_footer.docx` — Different first-page header/footer
- `poi_heading123.docx` — H1/H2/H3 heading hierarchy (259 words)
- `poi_complex_lists.docx` — Multi-level numbered and bulleted lists (52 words)
- `poi_checkboxes_sdt.docx` — Content controls (w:sdt) with checkboxes
- `poi_field_codes.docx` — Field codes (w:fldChar / w:instrText)
- `poi_fld_simple_toc.docx` — Simple TOC field (w:fldSimple)
- `poi_drawing.docx` — Inline drawing/image elements (7092 words)
- `poi_styles.docx` — Named paragraph and character styles
- `poi_table_footnotes.docx` — Table containing footnote references
- `poi_tracked_changes_delins.docx` — Tracked deletions and insertions (223 words)
- `poi_track_changes_on.docx` — Document with change tracking enabled

## Pandoc test suite (Category 2 — open-source DOCX test corpus)

Source: https://github.com/jgm/pandoc/tree/main/test/docx

- `pandoc_track_del.docx` — Tracked deletions
- `pandoc_track_ins.docx` — Tracked insertions
- `pandoc_track_move.docx` — Tracked text moves
- `pandoc_comments.docx` — Inline reviewer comments (31 words)
- `pandoc_sdt_footnote.docx` — SDT elements inside footnotes
- `pandoc_image.docx` — Inline image embedding
- `pandoc_image_vml.docx` — VML-based image (legacy drawing format)
- `pandoc_lists.docx` — List paragraphs (22 words)
- `pandoc_enum_headings.docx` — Enumerated heading styles
- `pandoc_table_captions.docx` — Table with captions (15 words)

## Mammoth.js test suite (Category 2 — open-source DOCX test corpus)

Source: https://github.com/mwilliamson/mammoth.js/tree/master/test/test-data

- `mammoth_endnotes.docx` — Endnotes in w:endnotes.xml
- `mammoth_footnotes.docx` — Footnotes in w:footnotes.xml
- `mammoth_tables.docx` — Table structure (10 words)
- `mammoth_textbox.docx` — Text box frames (4 words)
- `mammoth_comments.docx` — Comment annotations
- `mammoth_list.docx` — Simple list paragraphs

## python-docx test suite (Category 2)

Source: https://github.com/python-openxml/python-docx/tree/master/tests/test_files

- `pydocx_having_images.docx` — Document with embedded images
- `pydocx_test.docx` — Basic structure corpus document

## LibreOffice test suite (Category 2 + 3 — feature-targeted)

Source: https://github.com/LibreOffice/core/tree/master/sw/qa/extras/ooxmlexport/data  
and: https://github.com/LibreOffice/core/tree/master/sw/qa/extras/ooxmlimport/data

- `lo_watermark.docx` — VML picture watermark in header (Category 3: watermark coverage)
- `lo_toc_field.docx` — TOC implemented as w:fldChar field (Category 3: toc.py coverage)
- `lo_toc_preserve.docx` — TOC field preservation through round-trip
- `lo_toc_styles.docx` — TOC using custom style references
- `lo_toc_with_styles.docx` — TOC with `\s` and `\d` switches
- `lo_toc_no_numbers.docx` — TOC without page numbers
- `lo_toc_nonumbers.docx` — TOC variant without numbers
- `lo_sdt_content.docx` — SDT content control elements (Category 3: contentcontrols.py)
- `lo_groupshape_sdt.docx` — SDT inside a group shape
- `lo_textbox.docx` — WPS text box (9 words)
- `lo_chart.docx` — Embedded chart element (Category 3: charts.py)
