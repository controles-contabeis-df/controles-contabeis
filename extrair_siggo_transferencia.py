"""
Extrai a tabela MIL2026.TRANSFERENCIA (SIGGO) para df_siggo_transferencia.csv,
usada na aba "SIGGO" do painel TransfereGov x GDF.

Traz TODOS os registros da tabela (sem filtro de papel concedente/convenente -
ver memoria project_transferegov_siggo_chave: os campos que poderiam indicar
o papel, como COCONCENTE, nao se mostraram confiaveis isoladamente). O
cruzamento com o TransfereGov e o filtro fino ficam para uma etapa posterior.
"""

import oracledb
import pandas as pd
from pathlib import Path

PASTA = Path(__file__).resolve().parent

ORACLE_USER = "usefp79"
ORACLE_PASS = "bo39ra"
ORACLE_DSN = "10.69.1.118:1521/oraprd06"
INSTANT_CLIENT_DIR = r"C:\oracle\instantclient_23_0"


def main():
    oracledb.init_oracle_client(lib_dir=INSTANT_CLIENT_DIR)
    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN)

    print("Extraindo MIL2026.TRANSFERENCIA...")
    df = pd.read_sql(
        """
        SELECT
            NUTRANSFERENCIA, INESPECIE, INESPECIESIAFI, COCONCENTE, COBENEFICIADO,
            NUORIGINAL, NUTRANSFSIAFI, DACELEBRACAO, DAPUBLICACAO, DAINIVIGENCIA,
            DAFIMVIGENCIA, DARESCISAO, DACONCLUSAO, DAPRESTACONTA, DAEFETPRESTACONTA,
            COUG, COGESTAO, TXOBJETORESUMIDO, VATRANSFERENCIA, VACONTRAPARTIDA,
            NOGESTORBEN, NOEXECUTOR, INSTATUS, INADIMPLENTE, NUPROCESSO,
            COBANCO, COAGENCIA, NUCONTA
        FROM MIL2026.TRANSFERENCIA
        """,
        conn,
    )

    print("Extraindo MIL2026.UNIDADEGESTORA (para nomear UGs)...")
    df_ug = pd.read_sql(
        "SELECT COUG, NOUG, NORAZAOSOCIAL, NUCGC FROM MIL2026.UNIDADEGESTORA",
        conn,
    )

    saida = PASTA / "df_siggo_transferencia.csv"
    df.to_csv(saida, index=False, encoding="utf-8-sig")
    print(f"  -> {len(df)} registros salvos em {saida.name}")

    saida_ug = PASTA / "unidadegestora_bruto.csv"
    df_ug.to_csv(saida_ug, index=False, encoding="utf-8-sig")
    print(f"  -> {len(df_ug)} UGs salvas em {saida_ug.name}")


if __name__ == "__main__":
    main()
