# Changelog

## [0.4.0](https://github.com/d0ugal/ha-room-thermal-model/compare/v0.3.0...v0.4.0) (2026-10-26)


### Features

* step and effort power options in the back-test script ([63e310a](https://github.com/d0ugal/ha-room-thermal-model/commit/63e310ab34c9dd6b5de4752714ee6f5465370e88))


### Bug Fixes

* accept a slow loss rate as a well insulated house ([754ba8f](https://github.com/d0ugal/ha-room-thermal-model/commit/754ba8fb5f5a413a3a22d4d3eaf02d428fd8b408))
* floor the robust fit scale at the sensor noise ([5ac3d61](https://github.com/d0ugal/ha-room-thermal-model/commit/5ac3d61ef98226acadde66ff4a80628cb542cd53))
* run the model on a 15 minute grid ([f5e04bb](https://github.com/d0ugal/ha-room-thermal-model/commit/f5e04bb3912132e1c4857f9cf17136a12141496d))
* skip the maximum reachable check for a slow loss rate ([409a23e](https://github.com/d0ugal/ha-room-thermal-model/commit/409a23e20c9be8256a33e43302db1ef503bef463))


### Documentation

* explain the grid step, the scale floor and slow-loss trust ([f637ef6](https://github.com/d0ugal/ha-room-thermal-model/commit/f637ef69d023ca5e2b15007b1a3b568eaac32d14))

## [0.3.0](https://github.com/d0ugal/ha-room-thermal-model/compare/v0.2.2...v0.3.0) (2026-10-26)


### Features

* add a privacy scanner for commits ([2a3821f](https://github.com/d0ugal/ha-room-thermal-model/commit/2a3821f6fa3c5e693f95ffa8e890c41906db4de4))
* add offline back-test script for preheat run scoring and calibration ([144e790](https://github.com/d0ugal/ha-room-thermal-model/commit/144e790895cdf0683406fd62844f6a1e580ea426))
* add run-based calibration factor and keep raw and published predictions in scoring ([dd292b7](https://github.com/d0ugal/ha-room-thermal-model/commit/dd292b7cb44b692ae86d1f12dabf806be8de9596))
* add self_calibrate option ([2bd014a](https://github.com/d0ugal/ha-room-thermal-model/commit/2bd014a3102f0bfed96a46ab14575d4638df8bbf))
* **config:** add a zone_group option to the options flow ([a62a1f9](https://github.com/d0ugal/ha-room-thermal-model/commit/a62a1f907c87d74952a45ebfa02e28349d74f881))
* configure a schedule entity and target key ([a48f313](https://github.com/d0ugal/ha-room-thermal-model/commit/a48f3135efabc489f77f585ef2f1f81ed584e00f))
* **core:** assess whether a fit can be trusted ([a1a650f](https://github.com/d0ugal/ha-room-thermal-model/commit/a1a650fc269c540ca857ce93d938fb1cd452b802))
* **core:** build a fit prior from the mature rooms of a zone group ([67b13a7](https://github.com/d0ugal/ha-room-thermal-model/commit/67b13a7b2a5dbf0f78ab78a9253072f194c33e89))
* import_history service to load a JSON-lines history file ([7fa3e83](https://github.com/d0ugal/ha-room-thermal-model/commit/7fa3e838e60eb8142e33e850df1cf572d6cec6c0))
* parse JSON-lines history files into samples ([69e4caf](https://github.com/d0ugal/ha-room-thermal-model/commit/69e4cafe10cbbc8769cfa2507903c1d3f557b5a9))
* publish self-calibrated preheat minutes and lead time ([a176fd6](https://github.com/d0ugal/ha-room-thermal-model/commit/a176fd632195b5e9444bda572f59f2e5ae69f8c1))
* pure next-rise finder for weekly schedules ([7598838](https://github.com/d0ugal/ha-room-thermal-model/commit/7598838a47c093b1361d3d1fa47f703c7e5d1ef1))
* script to export history from a Prometheus-compatible API ([f136883](https://github.com/d0ugal/ha-room-thermal-model/commit/f1368832953051ea819c6f7666abc091b25cf248))
* share a fit prior between zones of the same zone group ([55402ea](https://github.com/d0ugal/ha-room-thermal-model/commit/55402ea9900fd81102521c5af80505ffcbb23ec3))
* use the next schedule block as the preheat arrival ([8cbe4dd](https://github.com/d0ugal/ha-room-thermal-model/commit/8cbe4dd6985d3c2ab624f529196b6f32ec40896e))
* withhold the preheat estimate when the fit is untrusted ([284fb31](https://github.com/d0ugal/ha-room-thermal-model/commit/284fb31d8e20afca2f221375abc533d0ad6dfb9f))


### Bug Fixes

* check commit authors only, not the committer GitHub sets ([67384f3](https://github.com/d0ugal/ha-room-thermal-model/commit/67384f3cd3a1eb8b1d977c35bc8ef5ddaf2d2efe))
* skip the scanner's own synthetic examples ([2afc128](https://github.com/d0ugal/ha-room-thermal-model/commit/2afc1280d1671b231e9f392983598ba527456016))


### Performance Improvements

* run-length encode the stored model grid ([64a355d](https://github.com/d0ugal/ha-room-thermal-model/commit/64a355dd27d3c33dbf1b501e4e898632261405a0))


### Documentation

* describe importing history and the measured long-history result ([7b983ae](https://github.com/d0ugal/ha-room-thermal-model/commit/7b983ae9dc235380e726eb6feaf41bbe2474c916))
* describe schedule-aware preheat ([a45bf69](https://github.com/d0ugal/ha-room-thermal-model/commit/a45bf6936e5b0453b6899afadabfd1e9b88e899b))
* document self-calibration and the back-test script ([973a20b](https://github.com/d0ugal/ha-room-thermal-model/commit/973a20bcd99ffaa93ce5a98960b490701248b00e))
* document zone groups with the leave-one-room-out evidence ([ada39c2](https://github.com/d0ugal/ha-room-thermal-model/commit/ada39c29d8b56b707ab1a6b32ddefad20c4a7a3b))
* explain fit trust and the rejected cooling prior ([f3dff36](https://github.com/d0ugal/ha-room-thermal-model/commit/f3dff3644f943cc59b09b8a812335900fa03e9cc))
* note that the committer identity cannot be controlled ([dc454c6](https://github.com/d0ugal/ha-room-thermal-model/commit/dc454c6ccd33a9ea915a02d82a77c969ace2d1a6))
* state the public-repo privacy rules ([0b57f7f](https://github.com/d0ugal/ha-room-thermal-model/commit/0b57f7f5718436b0102a3ab5d74837e00c4e213b))

## [0.2.2](https://github.com/d0ugal/ha-room-thermal-model/compare/v0.2.1...v0.2.2) (2026-10-25)


### Documentation

* describe the automatic uv.lock refresh ([adfe2cc](https://github.com/d0ugal/ha-room-thermal-model/commit/adfe2ccaa34af5faefb76635100d22d14bdebd79))

## [0.2.1](https://github.com/d0ugal/ha-room-thermal-model/compare/v0.2.0...v0.2.1) (2026-10-25)


### Documentation

* add a development guide, fork note and fork install URL ([22f1e91](https://github.com/d0ugal/ha-room-thermal-model/commit/22f1e91e5985534b413e8be248b18e2c72b7d496))
* add AGENTS.md with CLAUDE.md symlink ([380428a](https://github.com/d0ugal/ha-room-thermal-model/commit/380428a6467b99d38415ca84ff267cfbcdf1400c))

## [0.2.0](https://github.com/d0ugal/ha-room-thermal-model/compare/v0.1.1...v0.2.0) (2026-10-25)


### Features

* expose run scoring through coordinator, storage and sensors ([489f4ce](https://github.com/d0ugal/ha-room-thermal-model/commit/489f4ceece48c4155372bad64c79ba84edf25be0))
* score live preheat predictions against what the room did ([21ec226](https://github.com/d0ugal/ha-room-thermal-model/commit/21ec2264b0dac5de374a08837dad38d2fc091b47))


### Documentation

* document run scoring ([3df3bf4](https://github.com/d0ugal/ha-room-thermal-model/commit/3df3bf4ab6f16959225e4f167cfb251349c7a157))

## [0.1.1](https://github.com/d0ugal/ha-room-thermal-model/compare/v0.1.0...v0.1.1) (2026-10-24)


### Bug Fixes

* skip heat-up episodes whose endpoint temperature is missing ([8f9ae4a](https://github.com/d0ugal/ha-room-thermal-model/commit/8f9ae4a2865476a92bebb409afce3db07a403aa8))
