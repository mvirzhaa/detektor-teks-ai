const fs = require('fs');
const path = require('path');
const { Pool } = require('pg');
const config = require('../config');

const pool = new Pool({ connectionString: config.databaseUrl, max: 10 });

async function migrate(retries = 20) {
  // schema.sql = modul Deteksi AI, schema-similarity.sql = modul Cek Kemiripan
  const sql = ['schema.sql', 'schema-similarity.sql']
    .map((f) => fs.readFileSync(path.join(__dirname, f), 'utf8'))
    .join('\n');
  for (let i = 1; i <= retries; i++) {
    try {
      await pool.query(sql);
      return;
    } catch (err) {
      if (i === retries) throw err;
      console.warn(`[db] menunggu database (${i}/${retries}): ${err.message}`);
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
}

module.exports = { pool, query: (text, params) => pool.query(text, params), migrate };
