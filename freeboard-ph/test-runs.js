/*! @license
 * Freeboard Test Runs Widget Plugin
 * Copyright 2023 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

(() => {
  freeboard.addStyle('.test-runs', 'white-space: normal; line-height: 1.5;');

  class TestRuns {
    constructor(settings) {
      this.containerElement_ = $('<div class="test-runs"></div>');
      this.state_ = {};
      this.settings_ = {};
      this.onSettingsChanged(settings);
    }

    updateState_() {
      while (this.containerElement_.firstChild) {
        this.containerElement_.removeChild(this.containerElement_.lastChild);
      }

      const data = this.state_.data;
      if (!data) {
        return;
      }

      for (const run of data) {
        const triggeredAt = (new Date(run.trigger * 1000)).toDateString();
        const url = run.html_url;

        let status, dot;
        if (run.passed && run.flaky) {
          status = 'flaky';
          dot = '🔵';
        } else if (run.passed) {
          status = 'passed';
          dot = '🟢';
        } else {
          status = 'failed';
          dot = '🔴';
        }

        this.containerElement_.append(
          $(`<a target="_blank" href="${url}" title="${triggeredAt}: ${status}">${dot}</a>`));
        this.containerElement_.append(' ');
      }
    }

    render(element) {
      $(element).append(this.containerElement_);
    }

    onSettingsChanged(settings) {
      this.settings_ = settings;
      this.updateState_();
    }

    onCalculatedValueChanged(settingName, newValue) {
      this.state_[settingName] = newValue;
      this.updateState_();
    }

    onDispose() {}

    getHeight() {
      return this.settings_.height;
    }
  }

  freeboard.loadWidgetPlugin({
    type_name: 'test_runs',
    display_name: 'Test Runs',
    settings: [
      {
        name: 'data',
        display_name: 'Test runs array',
        type: 'calculated',
      },
      {
        name: 'height',
        display_name: 'Height in rows',
        type: 'number',
        default_value: 3,
      },
    ],
    newInstance: (settings, newInstanceCallback) => {
      newInstanceCallback(new TestRuns(settings));
    },
  });
})();
