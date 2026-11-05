# Canadian Water Quality Analysis Map

**Live map:** https://laplacesfiend.github.io/canadian-water-quality-analysis-map/

## What it is
An interactive map of water testing sites across Canada. Click a site to see which pollutants were measured there. Pick one, and the map tells you if its levels are going up, going down, or staying the same over the years.

## Why I made it
Government water data comes as huge spreadsheets that are hard to read. I wanted anyone to be able to explore it without needing to be a data person.

## How it works
- Every testing site is shown on the map and can be filtered by river basin
- Clicking a site loads that site's measurements
- A statistics test called Seasonal Kendall checks whether a pollutant is really trending up or down. It only compares the same month across different years, so normal seasonal changes (like spring melt) don't get mistaken for a trend
- The result shows up as a chart and a simple answer: increasing, decreasing, or no significant trend

## Built with
Python, JavaScript, Leaflet, OpenFreeMap, and PapaParse. Hosted free on GitHub Pages.

## ArcGIS Online Version
I first built this project in ArcGIS Online using Experience Builder. [View the ArcGIS version here](https://experience.arcgis.com/experience/6c9635cd96cd43d7bf8bbc86947be050).

The datasets were so large that the server took a long time to load the charts. To fix that, I rebuilt the project from scratch in Python and JavaScript. It now runs right in your browser, so there's no more waiting on a server.

## Data
Environment and Climate Change Canada water quality monitoring data: [National Long-term Water Quality Monitoring Data](https://data-donnees.az.ec.gc.ca/data/substances/monitor/national-long-term-water-quality-monitoring-data/)
