-- Add live-stream to the questionnaire type CHECK constraint
ALTER TABLE questionnaires DROP CONSTRAINT questionnaires_type_check;
ALTER TABLE questionnaires ADD CONSTRAINT questionnaires_type_check CHECK (type IN (
  'multiple-choice', 'multi-select', 'confirm', 'rich-choice',
  'toggle', 'hold-button', 'multi-live', 'button-grid', 'combo',
  'live-stream'
));
