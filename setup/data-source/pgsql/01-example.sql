-- Step 1 of 2: source S1 of the running example (PostgreSQL, database `reporting`).
-- Small dummy data for validating the Plan logic of ASCAM, table by table (see
-- docs/running-example.md for the scenario that uses each table). `person` and the view
-- v.v_active over it are Example 1 of the paper; the other tables isolate one Plan branch each,
-- so that the scenarios do not affect one another.

-- Example 1 of the paper: Ana and Budi are active, Cici is not
CREATE TABLE public.person (
    id         INTEGER PRIMARY KEY,
    active     BOOLEAN NOT NULL,
    first_name VARCHAR(50),
    nickname   VARCHAR(50)
);
INSERT INTO public.person VALUES (1, TRUE, 'Ana', 'Nana'), (2, TRUE, 'Budi', 'Bud'), (3, FALSE, 'Cici', 'Ci');

-- P-002: one triples map fills ex:phone from two columns; fax is only projected
CREATE TABLE public.contact (
    id     INTEGER PRIMARY KEY,
    phone  VARCHAR(20),
    phone2 VARCHAR(20),
    email  VARCHAR(50),
    fax    VARCHAR(20)
);
INSERT INTO public.contact VALUES (1, '0811-1', '0812-1', 'ana@example.org', '021-1'),
                                  (2, '0811-2', NULL, 'budi@example.org', NULL);

-- notification: discount is used inside an expression of view v.v_net_price
CREATE TABLE public.product (
    id       INTEGER PRIMARY KEY,
    label    VARCHAR(50),
    price    NUMERIC(10,2),
    discount NUMERIC(4,2)
);
INSERT INTO public.product VALUES (1, 'Pen', 10000, 0.10), (2, 'Book', 25000, 0.00);

-- notification: view v.v_note_body projects only body, so it would lose all its columns
CREATE TABLE public.note (id INTEGER PRIMARY KEY, body VARCHAR(100));
INSERT INTO public.note VALUES (1, 'first note'), (2, 'second note');

-- notification: view v.v_memo declares its columns inline
CREATE TABLE public.memo (id INTEGER PRIMARY KEY, body VARCHAR(100));
INSERT INTO public.memo VALUES (1, 'first memo'), (2, 'second memo');

-- notification: view v.v_doc_cache is materialized
CREATE TABLE public.doc (id INTEGER PRIMARY KEY, body VARCHAR(100));
INSERT INTO public.doc VALUES (1, 'first doc'), (2, 'second doc');

-- notification on RENAME: the foreign table already has a column reading `town`
CREATE TABLE public.staff (id INTEGER PRIMARY KEY, city VARCHAR(50));
INSERT INTO public.staff VALUES (1, 'Makassar'), (2, 'Depok');

-- P-001, X is not empty: rr:tableName map with one class; v.v_city_all selects * from it
CREATE TABLE public.city (id INTEGER PRIMARY KEY, name VARCHAR(50));
INSERT INTO public.city VALUES (1, 'Makassar'), (2, 'Depok');

-- P-001, several classes: three maps over the table, two classes
CREATE TABLE public.account (id INTEGER PRIMARY KEY, owner VARCHAR(50));
INSERT INTO public.account VALUES (1, 'Ana'), (2, 'Budi');

-- P-001, notification: view v.v_events selects * from it in a UNION
CREATE TABLE public.event (id INTEGER PRIMARY KEY, kind VARCHAR(20));
INSERT INTO public.event VALUES (1, 'login'), (2, 'logout');

-- P-001, X_C is empty: federated, but no triples map reads it
CREATE TABLE public.audit (id INTEGER PRIMARY KEY, action VARCHAR(20));
INSERT INTO public.audit VALUES (1, 'insert'), (2, 'delete');

-- P-001, reuse: ex:weight is declared and deprecated in the ontology, as if column weight had
-- been dropped earlier; adding weight again reuses the property
CREATE TABLE public.tool (id INTEGER PRIMARY KEY, label VARCHAR(50));
INSERT INTO public.tool VALUES (1, 'Hammer'), (2, 'Saw');
