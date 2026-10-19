"""
Copia diaria das extracoes (CSVs) antes de uma nova rodada de extracao.
Gera historico/extracoes_AAAAMMDD.zip e mantem apenas as 14 copias mais recentes.
Se rodar mais de uma vez no dia, sobrescreve a copia do dia (a primeira do dia e a
que importa; por isso a rotina chama este script antes das extracoes).
"""

import zipfile
from datetime import datetime
from pathlib import Path

PASTA = Path(__file__).resolve().parent
DESTINO = PASTA / "historico"
RETENCAO_DIAS = 14


def main():
    DESTINO.mkdir(exist_ok=True)
    arquivos = sorted(PASTA.glob("df_*.csv")) + [p for p in [PASTA / "unidadegestora_bruto.csv", PASTA / "cnpjs_gdf.csv"] if p.exists()]
    zip_path = DESTINO / f"extracoes_{datetime.now():%Y%m%d}.zip"
    if zip_path.exists():
        print(f"Copia de hoje ja existe ({zip_path.name}); mantida a primeira do dia.")
    else:
        tmp = zip_path.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for a in arquivos:
                z.write(a, a.name)
        tmp.replace(zip_path)
        print(f"Copia criada: {zip_path.name} ({len(arquivos)} arquivos, {zip_path.stat().st_size / 1e6:.1f} MB)")
    copias = sorted(DESTINO.glob("extracoes_*.zip"))
    for antigo in copias[:-RETENCAO_DIAS]:
        antigo.unlink()
        print(f"Removida copia antiga: {antigo.name}")


if __name__ == "__main__":
    main()
