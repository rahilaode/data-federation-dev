-- Step 1 of 2: source S2 of the running example (MySQL, database `registry`).
-- One small table, so that the federation has two autonomous sources: ex:nickname is filled
-- from both sources, and the MySQL schema monitor (scheduled snapshot) is exercised too.
USE registry;

CREATE TABLE member (
    member_id INT PRIMARY KEY,
    nama      VARCHAR(50),
    panggilan VARCHAR(50),
    phone     VARCHAR(20)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO member VALUES (10, 'Dewi', 'Wi', '0855-1'), (11, 'Eko', 'Ko', '0855-2');
