const SETTINGS = {
  sheetName: 'Queue',
  endpointUrl: 'https://example.com/aivideo', // Replace with deployed API Gateway URL.
  processedKey: 'dispatched_rows',
};

function processScheduledVideos() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SETTINGS.sheetName);
  if (!sheet) {
    throw new Error(`Sheet "${SETTINGS.sheetName}" was not found.`);
  }

  const values = sheet.getDataRange().getValues();
  if (values.length <= 1) {
    return;
  }

  const header = values[0].map((value) => value.toString().trim().toLowerCase());
  const urlIndex = header.indexOf('url');
  const scheduleIndex = header.indexOf('publish schedule datetime');
  if (urlIndex === -1 || scheduleIndex === -1) {
    throw new Error('Expected header row with "url" and "publish schedule datetime" columns.');
  }

  const docProps = PropertiesService.getDocumentProperties();
  const processed = new Set(JSON.parse(docProps.getProperty(SETTINGS.processedKey) || '[]'));

  const now = new Date();
  const timezone = SpreadsheetApp.getActive().getSpreadsheetTimeZone();
  const formatTimestamp = (date) => Utilities.formatDate(date, timezone, "yyyy-MM-dd'T'HH:mm:ssXXX");

  for (let row = 1; row < values.length; row += 1) {
    const rowNumber = row + 1;
    if (processed.has(rowNumber)) {
      continue;
    }

    const url = values[row][urlIndex];
    if (!url) {
      continue;
    }

    const scheduleCell = values[row][scheduleIndex];
    const scheduledAt = normaliseDate(scheduleCell, timezone);
    if (!scheduledAt) {
      annotateCell(sheet, rowNumber, scheduleIndex + 1, 'Invalid datetime value');
      continue;
    }

    if (scheduledAt > now) {
      continue;
    }

    try {
      const payload = {
        url: url.toString().trim(),
        scheduledAt: scheduledAt.toISOString(),
        rowNumber,
      };

      invokeEndpoint(payload);
      annotateCell(sheet, rowNumber, scheduleIndex + 1, `Dispatched at ${formatTimestamp(now)}`);
      processed.add(rowNumber);
    } catch (error) {
      annotateCell(sheet, rowNumber, scheduleIndex + 1, `Dispatch failed: ${error.message}`);
    }
  }

  docProps.setProperty(SETTINGS.processedKey, JSON.stringify(Array.from(processed)));
}

function normaliseDate(value, timezone) {
  if (value instanceof Date) {
    return value;
  }

  if (typeof value === 'string') {
    const parsed = new Date(value);
    if (!isNaN(parsed.getTime())) {
      return parsed;
    }
  }

  const parsedSerial = Number(value);
  if (!Number.isNaN(parsedSerial)) {
    return new Date(parsedSerial);
  }

  return null;
}

function annotateCell(sheet, row, column, message) {
  sheet.getRange(row, column).setNote(message);
}

function invokeEndpoint(payload) {
  const response = UrlFetchApp.fetch(SETTINGS.endpointUrl, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });

  if (response.getResponseCode() >= 300) {
    throw new Error(`Endpoint returned ${response.getResponseCode()}: ${response.getContentText()}`);
  }
}

function resetDispatchedRows() {
  PropertiesService.getDocumentProperties().deleteProperty(SETTINGS.processedKey);
}

function installScheduleTrigger() {
  ScriptApp.newTrigger('processScheduledVideos').timeBased().everyMinutes(15).create();
}
