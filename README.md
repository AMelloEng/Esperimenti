# Esperimenti

## Agente di Validazione — File Anagrafico (buoni pasto elettronici)

Script Python per il controllo qualità di file anagrafici a larghezza fissa (posizionale)
usati come input per la gestione dei buoni pasto elettronici (struttura SAP ZTMIT105).

### Requisiti

- Python 3.10+
- Nessuna dipendenza esterna (solo libreria standard)

### Utilizzo

```bash
python validatore_anagrafico.py <file.txt> [opzioni]
```

#### Opzioni

| Opzione | Descrizione |
|---|---|
| `--export-dir DIR` | Cartella di destinazione per i CSV di anomalie (default: stessa cartella del file) |
| `--encoding ENC` | Encoding del file di input (default: `utf-8`) |
| `--no-export` | Produce solo il report a terminale, senza esportare CSV |

#### Esempio

```bash
python validatore_anagrafico.py anagrafica_2024.txt --export-dir ./output --encoding latin-1
```

### Tracciato record (244 caratteri/riga)

| # | Campo | Pos. inizio (1-based) | Pos. fine | Lunghezza |
|---|---|---|---|---|
| 1 | CODF (Codice Fiscale) | 1 | 16 | 16 |
| 2 | CID (PERNR) | 17 | 24 | 8 |
| 3 | Nome | 25 | 64 | 40 |
| 4 | Cognome | 65 | 104 | 40 |
| 5 | UPN (e-mail) | 105 | 164 | 60 |
| 6-12 | Società, sede, focal point, … | 165 | 244 | resto |

### Controlli eseguiti

1. **CF — Pattern e omocodia**: verifica che il Codice Fiscale rispetti il pattern ufficiale,
   tollerando la sostituzione di cifre con lettere (omocodia). Controlla separatamente il
   check-digit.
2. **CF duplicati**: segnala ogni CF presente in più di una riga, con elenco delle righe.
3. **Stesso CF → CID diversi**: stesso CF associato a matricole (PERNR) differenti.
4. **Stesso CID → CF diversi**: stessa matricola associata a Codici Fiscali differenti.
5. **UPN mancante o incompleta**: campo e-mail assente o privo del carattere `@`.

### Output

Il report viene stampato a terminale. Per ogni anomalia rilevata vengono esportati CSV
separati (con prefisso `<nome_file>_`) pronti per la bonifica:

| File CSV | Contenuto |
|---|---|
| `*_anomalie_lunghezza.csv` | Righe con lunghezza diversa da 244 |
| `*_cf_pattern_errato.csv` | CF con pattern non valido |
| `*_cf_checkdigit_errato.csv` | CF con check-digit errato |
| `*_cf_duplicati.csv` | CF presenti in più righe |
| `*_cf_multi_cid.csv` | CF con CID diversi |
| `*_cid_multi_cf.csv` | CID con CF diversi |
| `*_upn_mancante.csv` | Righe con UPN assente o incompleta |

### Test

```bash
python -m unittest tests/test_validatore.py -v
```