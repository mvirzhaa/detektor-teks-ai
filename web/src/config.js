const int = (v, d) => (Number.isFinite(parseInt(v, 10)) ? parseInt(v, 10) : d);

module.exports = {
  port: int(process.env.PORT, 3000),
  databaseUrl: process.env.DATABASE_URL || 'postgresql://detektor:detektor@localhost:5432/detektor',
  mlUrl: (process.env.ML_URL || 'http://localhost:8000').replace(/\/$/, ''),
  basePath: (process.env.BASE_PATH || '').replace(/\/+$/, ''),
  // Bila diisi, semua aksi yang mengubah model/dataset wajib menyertakan token ini
  learnToken: process.env.LEARN_TOKEN || '',
  maxUploadMb: int(process.env.MAX_UPLOAD_MB, 15),
  maxImportMb: int(process.env.MAX_IMPORT_MB, 50),
  minWordsCheck: int(process.env.MIN_WORDS_CHECK, 15),
  minWordsLearn: int(process.env.MIN_WORDS_LEARN, 20),
  // Impor massal >= nilai ini memicu latih ulang penuh, bukan belajar incremental
  bulkRetrainThreshold: int(process.env.BULK_RETRAIN_THRESHOLD, 100),
  storeCheckText: process.env.STORE_CHECK_TEXT !== 'false',
};
