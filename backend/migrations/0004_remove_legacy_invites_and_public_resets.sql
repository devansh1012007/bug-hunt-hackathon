-- The event no longer uses invitation tokens or participant-facing reset tokens.
-- Admin registration creates passwordless accounts; first sign-in sets the password.
-- Drop legacy token stores so outstanding tokens cannot be reused accidentally.
DROP TABLE IF EXISTS app.invites;
DROP TABLE IF EXISTS app.password_resets;
