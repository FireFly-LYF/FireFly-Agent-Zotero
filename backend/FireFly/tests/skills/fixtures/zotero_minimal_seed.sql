-- Minimal schema aligned with official Zotero: parent links on itemNotes/itemAttachments,
-- not on items.parentItemID.
CREATE TABLE items (
    itemID INTEGER PRIMARY KEY,
    itemTypeID INTEGER NOT NULL,
    key TEXT NOT NULL,
    dateModified TEXT
);
CREATE TABLE itemTypes (
    itemTypeID INTEGER PRIMARY KEY,
    typeName TEXT NOT NULL
);
CREATE TABLE fields (
    fieldID INTEGER PRIMARY KEY,
    fieldName TEXT NOT NULL
);
CREATE TABLE itemDataValues (
    valueID INTEGER PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE itemData (
    itemID INTEGER NOT NULL,
    fieldID INTEGER NOT NULL,
    valueID INTEGER NOT NULL,
    PRIMARY KEY (itemID, fieldID)
);
CREATE TABLE creators (
    creatorID INTEGER PRIMARY KEY,
    firstName TEXT,
    lastName TEXT
);
CREATE TABLE creatorTypes (
    creatorTypeID INTEGER PRIMARY KEY,
    creatorType TEXT NOT NULL
);
CREATE TABLE itemCreators (
    itemID INTEGER NOT NULL,
    creatorID INTEGER NOT NULL,
    creatorTypeID INTEGER NOT NULL,
    orderIndex INTEGER NOT NULL
);
CREATE TABLE tags (
    tagID INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE itemTags (
    itemID INTEGER NOT NULL,
    tagID INTEGER NOT NULL
);
CREATE TABLE itemNotes (
    itemID INTEGER PRIMARY KEY,
    parentItemID INTEGER,
    note TEXT,
    title TEXT
);
CREATE TABLE itemAttachments (
    itemID INTEGER PRIMARY KEY,
    parentItemID INTEGER NOT NULL,
    linkMode INTEGER NOT NULL,
    contentType TEXT,
    path TEXT
);
CREATE TABLE itemAnnotations (
    itemID INTEGER PRIMARY KEY,
    parentItemID INTEGER NOT NULL,
    type INTEGER NOT NULL,
    text TEXT,
    comment TEXT,
    color TEXT,
    pageLabel TEXT,
    sortIndex TEXT NOT NULL,
    position TEXT NOT NULL,
    isExternal INTEGER NOT NULL
);

INSERT INTO itemTypes VALUES (1, 'journalArticle'), (2, 'note'), (3, 'attachment');
INSERT INTO fields VALUES (1, 'title'), (2, 'date'), (3, 'note');
INSERT INTO creatorTypes VALUES (1, 'author');
INSERT INTO items VALUES
    (100, 1, 'ABCD1234', '2026-01-01'),
    (101, 2, 'NOTE0001', '2026-01-02'),
    (200, 1, 'EFGH5678', '2026-01-03'),
    (300, 3, 'PDFKEY01', '2026-01-04');
INSERT INTO itemNotes VALUES (101, 100, 'User note text', NULL);
INSERT INTO itemDataValues VALUES (1, 'Attention Is All You Need'), (2, '2017'), (4, 'Other Paper');
INSERT INTO itemData VALUES
    (100, 1, 1),
    (100, 2, 2),
    (200, 1, 4);
INSERT INTO creators VALUES (1, 'Ashish', 'Vaswani');
INSERT INTO itemCreators VALUES (100, 1, 1, 0);
INSERT INTO tags VALUES (1, 'transformer');
INSERT INTO itemTags VALUES (100, 1);
INSERT INTO itemAttachments VALUES (300, 100, 0, 'application/pdf', 'ATTACH/pdf.pdf');
INSERT INTO itemAnnotations VALUES
    (400, 300, 0, 'scaled dot-product', '', '#ffd400', '3', '0', '{}', 0);
