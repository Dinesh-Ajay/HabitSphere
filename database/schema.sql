CREATE DATABASE IF NOT EXISTS habit_tracker;
USE habit_tracker;

CREATE TABLE IF NOT EXISTS USERS (
    user_id INT AUTO_INCREMENT PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    email VARCHAR(150) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS PASSWORD_RESET_TOKENS (
    reset_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    otp_hash VARCHAR(64) NOT NULL,
    otp_expires_at DATETIME NOT NULL,
    otp_verified BOOLEAN NOT NULL DEFAULT FALSE,
    attempts INT NOT NULL DEFAULT 0,
    reset_token_hash VARCHAR(64) NULL,
    reset_token_expires_at DATETIME NULL,
    used BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_reset_user (user_id),
    KEY idx_reset_token_hash (reset_token_hash),
    CONSTRAINT fk_reset_user
        FOREIGN KEY (user_id) REFERENCES USERS(user_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS HABITS (
    habit_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    habit_name VARCHAR(120) NOT NULL,
    description TEXT,
    category VARCHAR(80),
    goal_type ENUM('daily', 'weekly', 'monthly') NOT NULL,
    target_count INT NOT NULL,
    start_date DATE NOT NULL,
    status VARCHAR(30) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_habits_user
        FOREIGN KEY (user_id) REFERENCES USERS(user_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS HABIT_COMPLETION (
    completion_id INT AUTO_INCREMENT PRIMARY KEY,
    habit_id INT NOT NULL,
    completion_date DATE NOT NULL,
    completion_time TIME NULL,
    completed BOOLEAN NOT NULL,
    completion_count INT NOT NULL,
    notes TEXT,
    CONSTRAINT fk_completion_habit
        FOREIGN KEY (habit_id) REFERENCES HABITS(habit_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS HABIT_REMINDERS (
    reminder_id INT AUTO_INCREMENT PRIMARY KEY,
    habit_id INT NOT NULL,
    user_id INT NOT NULL,
    reminder_type ENUM('daily', 'weekly', 'monthly') NOT NULL,
    reminder_date DATE NOT NULL,
    sent_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_habit_reminder_period
        UNIQUE (habit_id, reminder_type, reminder_date),
    KEY idx_reminder_habit_owner (habit_id, user_id),
    KEY idx_reminder_user_period (user_id, reminder_date, reminder_type),
    CONSTRAINT fk_reminder_user
        FOREIGN KEY (user_id) REFERENCES USERS(user_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_reminder_habit_owner
        FOREIGN KEY (habit_id, user_id) REFERENCES HABITS(habit_id, user_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS HABIT_ANALYTICS (
    analytics_id INT AUTO_INCREMENT PRIMARY KEY,
    habit_id INT NOT NULL,
    completion_percentage DECIMAL(5,2) NOT NULL,
    current_streak INT NOT NULL,
    longest_streak INT NOT NULL,
    consistency_score DECIMAL(5,2) NOT NULL,
    success_rate DECIMAL(5,2) NOT NULL,
    report_week INT,
    report_month INT,
    last_updated DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_analytics_habit
        FOREIGN KEY (habit_id) REFERENCES HABITS(habit_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS IMPROVEMENT_TIPS (
    tip_id INT AUTO_INCREMENT PRIMARY KEY,
    habit_id INT NOT NULL,
    suggestion TEXT NOT NULL,
    priority VARCHAR(30) NOT NULL,
    generated_date DATE NOT NULL,
    CONSTRAINT fk_tips_habit
        FOREIGN KEY (habit_id) REFERENCES HABITS(habit_id)
        ON DELETE CASCADE
);