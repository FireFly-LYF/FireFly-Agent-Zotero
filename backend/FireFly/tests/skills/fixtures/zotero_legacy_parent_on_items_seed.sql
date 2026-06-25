-- Legacy test fixture: parentItemID on items (not official Zotero layout).
CREATE TABLE items (
    itemID INTEGER PRIMARY KEY,
    itemTypeID INTEGER NOT NULL,
    key TEXT NOT NULL,
    parentItemID INTEGER,
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
    (100, 1, 'ABCD1234', NULL, '2026-01-01'),
    (101, 2, 'NOTE0001', 100, '2026-01-02');
INSERT INTO itemDataValues VALUES (1, 'Legacy Paper'), (2, '2020'), (3, 'Legacy note body');
INSERT INTO itemData VALUES
    (100, 1, 1),
    (100, 2, 2),
    (101, 3, 3);
INSERT INTO creators VALUES (1, 'Ada', 'Lovelace');
INSERT INTO itemCreators VALUES (100, 1, 1, 0);
