-- Run once in the existing dota_review database as its database administrator.
-- Runtime access stays narrow: SELECT all data, update only player role columns,
-- and write only inventory snapshot rows during replay backfill.
CREATE TABLE IF NOT EXISTS dota_inventory_snapshots (
    match_key VARCHAR(64) NOT NULL,
    slot INT NOT NULL,
    time_s INT NOT NULL,
    tick BIGINT NOT NULL,
    items_json LONGTEXT NOT NULL,
    PRIMARY KEY (match_key, slot, tick),
    KEY idx_dota_inventory_time (match_key, slot, time_s, tick),
    CONSTRAINT fk_dota_inventory_match FOREIGN KEY (match_key)
        REFERENCES dota_matches(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

GRANT SELECT ON dota_review.dota_inventory_snapshots TO 'dota_review_app'@'%';
GRANT INSERT, UPDATE, DELETE ON dota_review.dota_inventory_snapshots TO 'dota_review_app'@'%';
GRANT UPDATE (role, role_source) ON dota_review.dota_players TO 'dota_review_app'@'%';
