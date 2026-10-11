# Room Thermal Model

[English](README.md)

Integrazione generica per Home Assistant che **impara come si scalda e si raffredda ogni
stanza** dal tuo storico, e ti dice **quanti minuti di riscaldamento servono** per
raggiungere una temperatura obiettivo, così le automazioni possono accendere al momento
giusto.

Funziona con **qualsiasi entità `climate`** (valvole termostatiche, generic_thermostat,
Versatile Thermostat, termostati smart…) e legge solo attributi standard. Non comanda
niente: espone solo sensori.

## Come funziona

Ogni zona è una massa termica che riceve calore dal calorifero e lo perde verso
l'esterno (modello *RC* a una capacità), su una griglia di 5 minuti:

```
ΔT/Δt = a · sforzo(t − tempo morto) − b · (T_stanza − T_esterna) + c
```

| Parametro | Significato |
|---|---|
| `a` (K/h) | quanto velocemente il calorifero scalda la stanza a pieno regime |
| `b` (1/h) | dispersione verso l'esterno — costante di tempo τ = 1/b |
| `c` (K/h) | apporti interni e solari |
| tempo morto (min) | ritardo prima che il calorifero inizi a scaldare la stanza |

- **Apprendimento** da *tutto* lo storico, non solo dai preriscaldamenti: i periodi a
  riscaldamento spento insegnano la dispersione, quelli acceso la potenza. La stima ai
  minimi quadrati robusta (Huber) pesa poco le anomalie come una finestra aperta, e i
  dati recenti contano di più (emivita 21 giorni). Al primo avvio importa lo storico del
  recorder: con qualche giorno di storia è utile da subito.
- **Minuti di preriscaldamento** dalla soluzione esatta del modello:
  `t = tempo morto + τ · ln((T∞ − T_ora) / (T∞ − T_obiettivo))`, dove `T∞` è la massima
  temperatura raggiungibile con la temperatura esterna attuale. Con un'entità meteo la
  temperatura esterna è mediata sulla previsione della finestra di preriscaldamento. Se
  `T∞` è sotto l'obiettivo la zona risulta **non raggiungibile**.
- **Maturità**: confronta le previsioni a 60 minuti con la realtà e con una baseline
  banale ("la temperatura resta uguale"), e i tempi di riscaldamento previsti con quelli
  osservati.

Le idee per tempo morto, verifica sulle curve di raffreddamento e media della previsione
vengono da [Ecronika/ha-preheat](https://github.com/Ecronika/ha-preheat) (MIT, © 2025 Ecronika).

## Installazione

HACS → ⋮ → **Repository personalizzati** → `https://github.com/giogandola98/ha-room-thermal-model`,
categoria **Integrazione** → installa → riavvia Home Assistant.
Manuale: copia `custom_components/room_thermal_model/` nella cartella di configurazione.

Richiede Home Assistant 2026.1+ e il `recorder` (per importare lo storico).

## Configurazione (una voce per zona)

Impostazioni → Dispositivi e servizi → Aggiungi integrazione → **Room Thermal Model**.

| Ingresso | Obbligatorio | Note |
|---|---|---|
| Nome zona | sì | usato negli entity_id |
| Entità climate | sì | `current_temperature`, `hvac_action`, `temperature` obiettivo, modalità HVAC |
| Sensore temperatura stanza | no | sostituisce la temperatura del termostato (es. un sensore lontano dal calorifero) |
| Sensore sforzo di riscaldamento (%) | no | apertura valvola / richiesta di calore 0–100 %; altrimenti si usa `hvac_action` (heating = 100 %, idle/off = 0 %); se il climate non ha `hvac_action`, "modalità accesa e obiettivo > attuale" |
| Sensore temperatura esterna | no | valore attuale e storico |
| Entità meteo | no | previsione per la finestra di preriscaldamento (oraria → due volte al giorno → giornaliera) e riserva per la temperatura esterna |
| Sensore orario di arrivo | no | qualsiasi sensore timestamp (es. `sensor.house_next_return_eta` di [Home Return Predictor](https://github.com/giogandola98/ha-home-return-predictor)); abilita inizio preriscaldamento / preriscalda ora |

Opzioni: temperatura obiettivo (fissa, default 20,5 °C, oppure quella del termostato),
emivita dei dati (21 giorni), finestra di storico (30 giorni), intervallo di
aggiornamento (5 min). Senza nessuna fonte di temperatura esterna il modello funziona
lo stesso ma non segue il meteo: aspettati errori più grandi.

## Entità (per zona)

| Entità | Contenuto |
|---|---|
| `sensor.<zona>_preheat_minutes` | minuti di riscaldamento pieno per raggiungere l'obiettivo; sconosciuto mentre impara o se non raggiungibile (attributo `reason`) |
| `sensor.<zona>_max_reachable_temperature` | massima temperatura raggiungibile con la temperatura esterna attuale |
| `sensor.<zona>_model_maturity` | 0–100 %; attributi `rmse_c`, `baseline_rmse_c`, `duration_mae_minutes`, `heating_hours`, `cooling_hours` |
| `sensor.<zona>_preheat_start` | arrivo − minuti di preriscaldamento (solo con sensore di arrivo) |
| `binary_sensor.<zona>_preheat_now` | acceso quando è ora di iniziare a scaldare (solo con sensore di arrivo) |
| `sensor.<zona>_time_constant`, `_heating_rate`, `_dead_time` | diagnostica: parametri imparati |

Servizi: `room_thermal_model.retrain`, `room_thermal_model.reset`,
`room_thermal_model.export_diagnostics`. Supporta il download della diagnostica.

## Uso con Home Return Predictor + Versatile Thermostat

Nel blueprint VTherm di Home Return Predictor imposta l'ingresso "sensore minuti di
preriscaldamento" di ogni zona su `sensor.<zona>_preheat_minutes`. Il blueprint resta
l'unico a comandare VTherm; questa integrazione fornisce solo i minuti.

## Sviluppo

```bash
pip install -r requirements_test.txt
pytest                     # test del modello puro (senza HA) + test dell'integrazione
python scripts/simulate.py --days 30   # stanze sintetiche: curva di apprendimento
```

## Limiti

- Una sola massa termica per zona: scambi tra stanze vicine e forte sole sono colti solo
  in media.
- Lo sforzo da `hvac_action` è acceso/spento; un vero sensore di apertura valvola dà
  risultati più precisi.
- Senza temperatura esterna le stime non si adattano al meteo.
