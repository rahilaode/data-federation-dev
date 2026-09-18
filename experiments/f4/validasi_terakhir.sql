-- Rincian validasi pada eksekusi terakhir (dijalankan di basis data Knowledge).
SELECT v.id, v.execution_id, v.validator, v.passed,
       left(coalesce(v.details->>'output', v.details::text), 400) AS rincian
FROM ops.validation v
ORDER BY v.id DESC
LIMIT 10;
