-- Remove live-stream from the questionnaire type CHECK constraint
DELETE FROM responses WHERE questionnaire_id IN (SELECT id FROM questionnaires WHERE type = 'live-stream');
DELETE FROM questionnaires WHERE type = 'live-stream';
ALTER TABLE questionnaires DROP CONSTRAINT questionnaires_type_check;
ALTER TABLE questionnaires ADD CONSTRAINT questionnaires_type_check CHECK (type IN (
  'multiple-choice', 'multi-select', 'confirm', 'rich-choice',
  'toggle', 'hold-button', 'multi-live', 'button-grid', 'combo'
));
