"""
Bens Móveis (1231101XX / 1231108XX / 1231118XX) da UG 190101 (SEODF) — exercício 2026.
 1) Saldo inicial (INMES = 0) por conta.
 2) Lançamentos do ano nessas contas (VLANCAMENTOCONTABIL) e a(s) Nota(s) de Empenho
    citada(s) na conta corrente das demais linhas do MESMO documento (Documento + UG).
Somente leitura. Gera Excel em saida_bens_moveis_ne_190101.xlsx
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path
import oracledb, pandas as pd
from dotenv import load_dotenv

PASTA = Path(__file__).parent
load_dotenv(PASTA / ".env")
UG = 190101
FAIXAS = "(COCONTACONTABIL BETWEEN 123110100 AND 123110199 OR COCONTACONTABIL BETWEEN 123110800 AND 123110899 OR COCONTACONTABIL BETWEEN 123111800 AND 123111899)"

oracledb.init_oracle_client(lib_dir=r"C:\oracle\instantclient_23_0")
c = oracledb.connect(user=os.environ["ORACLE_USER"], password=os.environ["ORACLE_PASS"], dsn=os.environ["ORACLE_DSN"])
q = lambda sql, **p: pd.read_sql(sql, c, params=p)

saldo = q(f"""
SELECT s.COCONTACONTABIL CONTA, TRIM(cc.NOCONTACONTABIL) NOME_CONTA,
       SUM(s.VADEBITO) DEBITO, SUM(s.VACREDITO) CREDITO, SUM(s.VADEBITO - s.VACREDITO) SALDO_INICIAL
FROM MIL2026.VSALDOCONTABIL s LEFT JOIN MIL2026.VCONTACONTABIL cc ON cc.COCONTACONTABIL = s.COCONTACONTABIL
WHERE s.COUG = :ug AND s.INMES = 0 AND {FAIXAS.replace('COCONTACONTABIL','s.COCONTACONTABIL')}
GROUP BY s.COCONTACONTABIL, TRIM(cc.NOCONTACONTABIL) ORDER BY 1""", ug=UG)

lanc = q(f"""
SELECT l.NUDOCUMENTO, l.NULANCAMENTO, l.COEVENTO, l.COCONTACONTABIL CONTA, l.INDEBITOCREDITO DC,
       l.VALANCAMENTO VALOR, l.INMES MES, l.DALANCAMENTO DATA,
       (SELECT LISTAGG(t.NE, ';') WITHIN GROUP (ORDER BY t.NE)
        FROM (SELECT DISTINCT x.COUG, x.NUDOCUMENTO, x.COEVENTO,
                     REGEXP_SUBSTR(x.COCONTACORRENTE,'^[0-9]{{4}}NE[0-9]{{5}}') NE
              FROM MIL2026.VLANCAMENTOCONTABIL x
              WHERE x.COUG = :ug AND REGEXP_LIKE(x.COCONTACORRENTE,'^[0-9]{{4}}NE[0-9]{{5}}')) t
        WHERE t.COUG = l.COUG AND t.NUDOCUMENTO = l.NUDOCUMENTO AND t.COEVENTO = l.COEVENTO) NES_DO_DOCUMENTO
FROM MIL2026.VLANCAMENTOCONTABIL l
WHERE l.COUG = :ug AND l.COUGCONTAB = :ug AND {FAIXAS.replace('COCONTACONTABIL','l.COCONTACONTABIL')}
ORDER BY l.DALANCAMENTO, l.NUDOCUMENTO, l.NULANCAMENTO""", ug=UG)

nes = sorted({n for v in lanc["NES_DO_DOCUMENTO"].dropna() for n in v.split(";")})
if nes:
    ins = ",".join(f"'{n}'" for n in nes)
    ne = q(f"""SELECT n.NUNE, n.DAEMISSAO, n.COCREDOR, n.VADOCUMENTO VALOR_NE, n.CONATUREZA, n.COFONTE, n.COUO,
                      n.NUPROCESSO, n.NUCONTRATO, n.TXREFERENCIA
               FROM MIL2026.NOTAEMPENHO n WHERE n.COUG = :ug AND n.NUNE IN ({ins})""", ug=UG)
    ne["NUNE"] = ne["NUNE"].str.strip()
else:
    ne = pd.DataFrame()

c.close()
saida = PASTA / "saida_bens_moveis_ne_190101.xlsx"
with pd.ExcelWriter(saida) as w:
    saldo.to_excel(w, sheet_name="Saldo_inicial_2026", index=False)
    lanc.to_excel(w, sheet_name="Lancamentos_2026", index=False)
    ne.to_excel(w, sheet_name="NEs_citadas", index=False)

print("SALDO INICIAL:\n", saldo.to_string(index=False)); print("Total:", saldo["SALDO_INICIAL"].sum())
print("\nLANCAMENTOS:", len(lanc), "linhas;", lanc["NUDOCUMENTO"].nunique(), "documentos")
print(lanc.to_string(index=False))
print("\nNEs:\n", ne.to_string(index=False))
print("\nArquivo:", saida)
