-- 0004: HNSW index for the vector half of wholesaler.hybrid_rank.
-- Without it every search scans all 17.5k x 1024-dim embeddings: ~1.5 s on a laptop and above the
-- 2 s search timeout on Supabase free, so search_products fails ("Catalog search timed out").
-- With it: ~80 ms. Run once in the Supabase SQL editor.
-- Supabase free cannot allocate the shared memory a parallel build needs (ERROR 53200), so build
-- single-process with modest memory: 3-8 minutes, a 'graph no longer fits' notice is harmless.
set statement_timeout = '20min';
set max_parallel_maintenance_workers = 0;
set maintenance_work_mem = '64MB';

create index if not exists products_embedding_hnsw
  on wholesaler.products using hnsw (embedding extensions.vector_cosine_ops);

-- hybrid_rank asks for k = 300 neighbours; HNSW returns at most ef_search rows (default 40).
alter function wholesaler.hybrid_rank(text, text, int) set hnsw.ef_search = 300;
