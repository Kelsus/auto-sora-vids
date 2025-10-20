# Google Sheets Dispatch Script

This Apps Script watches a Google Sheet for article URLs that should be fed into the `aivideo` pipeline at or after a scheduled timestamp. When a row is due, the script posts the payload to the AWS Lambda API exposed by this project.

## Sheet Layout

Create a sheet (default name `Queue`) with the following header row:

| url | publish schedule datetime |
| --- | --- |

* Columns must use the exact header labels (case insensitive).
* Timestamps can be native Google Sheets datetime values or ISO-8601 strings. The script skips blank rows automatically.

## Script Setup

1. Open the sheet and go to **Extensions → Apps Script**.
2. Create a new project and replace the default contents with [`Code.gs`](Code.gs).
3. Update `SETTINGS.endpointUrl` with the deployed API Gateway URL.
4. Optional: rename `sheetName` if your tab is not called `Queue`.
5. Save the script and grant permissions on the first run.

### Scheduling

Run the `installScheduleTrigger` function once from the Apps Script editor. It registers a time-driven trigger that runs `processScheduledVideos` every 15 minutes. Adjust the interval if needed.

### Resetting Processed Rows

If you modify existing rows and want them reprocessed, run the `resetDispatchedRows` function. It clears the doc property that tracks delivered rows; the next scheduled run will re-send any rows whose publish time is due.

### Error Visibility

Any dispatch errors are written as a cell note on the `publish schedule datetime` column for the affected row. Fix the underlying issue (e.g., invalid URL or server error) and run `resetDispatchedRows` to retry.
