# Licences and terms of the upstream data

The code in this repository and the derived tables are offered for research and journalism. Each dataset keeps the licence or terms of its
upstream source, listed below; when you reuse a file, cite the original source as shown. Status column: **verified** = checked against the
provider's own licence statement on 2026-10-18; **stated** = taken from the provider's documentation as recorded at download time, not re-checked;
**uncertain** = no explicit licence found.

| dataset(s) | upstream source | licence / terms | status |
|---|---|---|---|
| fires/, fire_detections_* | NASA FIRMS (LANCE / MODAPS), MODIS C6.1 and VIIRS 375 m active fires | NASA Earth science data are open with no restrictions on use (NASA Earth Science Data and Information Policy). Cite: "NASA FIRMS, https://firms.modaps.eosdis.nasa.gov/" and the product DOIs (MODIS MCD14ML doi:10.5067/FIRMS/MODIS/MCD14ML; VIIRS VNP14IMGTDL_NRT doi:10.5067/FIRMS/VIIRS/VNP14IMGT_NRT.002). | stated |
| burned_area/, burned_area_*.tif | NASA MODIS MCD64A1 C6.1 (LP DAAC), via Microsoft Planetary Computer | NASA open data, no restrictions; cite doi:10.5067/MODIS/MCD64A1.061 (Giglio et al.). | stated |
| s2_night/, s2night_*.tif | ESA Copernicus Sentinel-2 night-time campaign (Dec 2025), CDSE; ESA WorldCover 2021 v200 for the setting | Copernicus Sentinel data: free, full and open (Copernicus data policy, Regulation (EU) 2021/696); credit "Contains modified Copernicus Sentinel data 2025". WorldCover: CC BY 4.0, cite "ESA WorldCover 2021 v200, doi:10.5281/zenodo.7254221". | stated |
| smoke_geosfp_*.nc | NASA GMAO GEOS-FP | NASA open data, no restrictions; cite NASA GMAO GEOS-FP. | stated |
| creams/, creams_events.* | ICAR-IARI CREAMS daily crop-residue burning bulletins (https://creams.iari.res.in/) | Public bulletins of a government research institute; no explicit licence found. Redistributed as factual extracts with source URLs; cite ICAR-IARI CREAMS and the bulletin. | uncertain |
| boundaries/districts (India part) | geoBoundaries gbOpen IND ADM2 (Pathways Data / lgdirectory.gov.in) | **Open Database License (ODbL) 1.0** - the districts layer is a derivative database and is released under ODbL 1.0; tables that use it for attribution are Produced Works (attribution required). | verified (geoBoundaries API) |
| boundaries/states (India part) | geoBoundaries gbOpen IND ADM1 (DataMeet India / Election Commission of India) | CC BY 2.5 India | verified (geoBoundaries API) |
| boundaries (Pakistan) | geoBoundaries gbOpen PAK ADM1/ADM2 | Public domain | verified (geoBoundaries API) |
| boundaries/subcontinent | Natural Earth 1:10m admin-0, India point of view | Public domain | stated (naturalearthdata.com terms) |
| air/cpcb_* | CPCB CAAQMS data redistributed by OpenAQ API v3 | OpenAQ terms: users must comply with each provider's terms and credit both the original provider (CPCB) and OpenAQ. CPCB data are Indian government data; no separate licence was found on the OpenAQ records. | verified (OpenAQ terms); CPCB licence uncertain |
| air/cams_era5_* | Open-Meteo Air Quality API (CAMS global forecasts) and Historical Weather API (ERA5/ERA5-Land) | Open-Meteo: CC BY 4.0 (attribute Open-Meteo). Underlying CAMS and C3S ERA5 data: Copernicus licence / CC BY 4.0 - "Contains modified Copernicus Atmosphere Monitoring Service and Climate Change Service information". | stated (Open-Meteo licence page is script-rendered and was not re-read) |
| air/stubble_share_delhi | IITM Pune (MoES): SAFAR, DSS, AQEWS public web pages, mostly via Internet Archive captures | Public government web pages; no explicit licence found. Cite IITM and the capture URL. | uncertain |
| paddy/ (1997-98 district rows) | DES Crop Production Statistics via the India Data Portal mirror | ODC-By (as recorded at download) | stated |
| paddy/ | UPAg (Unified Portal for Agricultural Statistics) / DES, Ministry of Agriculture & Farmers Welfare, Area-Production-Yield | Government Open Data License - India (GODL) is the default for Indian government open data; not stated on every UPAg download. | uncertain |
| enforcement/ | Lok Sabha / Rajya Sabha answers, CAQM directions and reports, NGT and Supreme Court filings, Punjab / Haryana departments and pollution control boards, data.gov.in (OGD) tables | Public government documents; data.gov.in resources are under GODL. Values are short factual extracts with quotes and source URLs. | data.gov.in verified as GODL by its portal terms; others uncertain |

## How to cite this release

Cite the repository (and release tag) plus the original source of each dataset you use, e.g. "NASA FIRMS active fire data (MODIS C6.1, VIIRS 375 m),
aggregated in <repository>, accessed <date>".

## Personal data

The release contains no personal data beyond the names of public offices and officials as they appear in official documents. CPCB station names and
village/tehsil names are place names.
