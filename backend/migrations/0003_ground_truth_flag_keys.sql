-- Flag keys are the stable award-unit identifiers used by the evaluator.
-- A challenge must not have two separate flag records sharing one award key.
CREATE UNIQUE INDEX IF NOT EXISTS uq_gt_flags_challenge_flag_key
    ON gt.gt_flags (challenge_version_id, flag_key);
