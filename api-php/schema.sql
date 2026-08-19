-- Схема заявок под MySQL 8. Перенос с SQLite (api/server.ts): те же поля,
-- те же значения по умолчанию. Ставится один раз через phpMyAdmin в ispmanager
-- или командой: mysql -u u3616302_default -p u3616302_default < schema.sql

CREATE TABLE IF NOT EXISTS leads (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  -- Время пишем строкой в ISO-8601 UTC, как в оригинале: так заявки из старой
  -- базы и из новой читаются одинаково, без догадок о часовом поясе сервера.
  created_at VARCHAR(32)  NOT NULL,
  name       VARCHAR(120) NOT NULL,
  contact    VARCHAR(120) NOT NULL,
  channel    VARCHAR(16)  NOT NULL,
  page       VARCHAR(255) NULL,
  geo        VARCHAR(120) NULL,
  source     VARCHAR(255) NULL,
  magnet     VARCHAR(64)  NULL,
  quiz       TEXT         NULL,
  utm        TEXT         NULL,
  ip         VARCHAR(45)  NOT NULL,
  delivered  TINYINT(1)   NOT NULL DEFAULT 0,
  INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Одноразовые ссылки на PDF лид-магнитов: файл не должен утечь в поисковый индекс
CREATE TABLE IF NOT EXISTS downloads (
  token      CHAR(32)    PRIMARY KEY,
  magnet     VARCHAR(64) NOT NULL,
  expires_at DATETIME    NOT NULL,
  used_at    DATETIME    NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Окно антифлуда. В Bun-версии счётчик жил в памяти процесса; PHP умирает после
-- каждого запроса, поэтому окно хранится здесь. Чистится на каждой заявке.
CREATE TABLE IF NOT EXISTS rate_hits (
  id     INT AUTO_INCREMENT PRIMARY KEY,
  ip     VARCHAR(45) NOT NULL,
  hit_at DATETIME    NOT NULL,
  INDEX idx_ip_time (ip, hit_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
