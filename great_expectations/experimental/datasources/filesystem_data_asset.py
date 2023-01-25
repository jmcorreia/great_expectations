from __future__ import annotations

import copy
import logging
import pathlib
import re
from typing import List, Optional, Tuple, Union

import pydantic

import great_expectations.exceptions as ge_exceptions
from great_expectations.core.batch_spec import PathBatchSpec
from great_expectations.experimental.datasources.interfaces import (
    Batch,
    BatchRequest,
    BatchRequestOptions,
    BatchSortersDefinition,
    DataAsset,
)

LOGGER = logging.getLogger(__name__)


class FilesystemDataAsset(DataAsset):
    """
    # TODO: <Alex>
        This temporary placeholder implementation of "Local Filestem based DataAsset" pertains to local filesystem only.
        Fully-functional implementation should be architecturally analogous to general inheritance hierarchy similar to:
        - ConfiguredAssetFilesystemDataConnector<-ConfiguredAssetFilePathDataConnector<-FilePathDataConnector
        - ConfiguredAssetS3DataConnector<-ConfiguredAssetFilePathDataConnector<-FilePathDataConnector
        - ConfiguredAssetAzureDataConnector<-ConfiguredAssetFilePathDataConnector<-FilePathDataConnector
        - ConfiguredAssetGCSDataConnector<-ConfiguredAssetFilePathDataConnector<-FilePathDataConnector
        with attention to cloud storage access protocols and corresponding "ExecutionEngine" support for retrieving
        "Batch" data as depicted in "ExecutionEngine.resolve_data_reference()" as well as to data reference caching.
    # TODO: </Alex>
    """

    base_directory: pathlib.Path
    regex: Union[str, re.Pattern]

    # Internal attributes
    _unnamed_regex_param_prefix: str = pydantic.PrivateAttr(
        default="batch_request_param_"
    )

    def __init__(
        self,
        name: str,
        base_directory: pathlib.Path,
        regex: Union[str, re.Pattern],
        order_by: Optional[BatchSortersDefinition] = None,
    ):
        """Constructs a "FilesystemDataAsset" object.

        Args:
            name: The name of the present File Path DataAsset
            base_directory: base directory path, relative to which file paths will be collected
            regex: regex pattern that matches filenames and whose groups are used to label the Batch samples
            order_by: one of "asc" (ascending) or "desc" (descending) -- the method by which to sort "Asset" parts.
        """
        super().__init__(
            name=name,
            order_by=order_by,
        )

        self.base_directory = base_directory  # type: ignore[arg-type]  # str will be coerced to Path
        self.regex = regex  # type: ignore[arg-type]  # str with will be coerced to Pattern

    def _fully_specified_batch_requests_with_path(
        self, batch_request: BatchRequest
    ) -> List[Tuple[BatchRequest, pathlib.Path]]:
        """Generates a list fully specified batch requests from partial specified batch request

        Args:
            batch_request: A batch request

        Returns:
            A list of pairs (batch_request, path) where 'batch_request' is a fully specified
            batch request and 'path' is the path to the corresponding file on disk.
            This list will be empty if no files exist on disk that correspond to the input
            batch request.
        """
        option_to_group_id = self._option_name_to_regex_group_id()
        group_id_to_option = {v: k for k, v in option_to_group_id.items()}
        batch_requests_with_path: List[Tuple[BatchRequest, pathlib.Path]] = []

        all_files: List[pathlib.Path] = list(
            pathlib.Path(self.base_directory).iterdir()
        )

        file_name: pathlib.Path
        for file_name in all_files:
            match = self.regex.match(file_name.name)
            if match:
                # Create the batch request that would correlate to this regex match
                match_options = {}
                for group_id in range(1, self.regex.groups + 1):
                    match_options[group_id_to_option[group_id]] = match.group(group_id)
                # Determine if this file_name matches the batch_request
                allowed_match = True
                for key, value in batch_request.options.items():
                    if match_options[key] != value:
                        allowed_match = False
                        break
                if allowed_match:
                    batch_requests_with_path.append(
                        (
                            BatchRequest(
                                datasource_name=self.datasource.name,
                                data_asset_name=self.name,
                                options=match_options,
                            ),
                            self.base_directory / file_name,
                        )
                    )
                    LOGGER.debug(f"Matching path: {self.base_directory / file_name}")
        if not batch_requests_with_path:
            LOGGER.warning(
                f"Batch request {batch_request} corresponds to no data files."
            )
        return batch_requests_with_path

    def batch_request_options_template(
        self,
    ) -> BatchRequestOptions:
        template: BatchRequestOptions = self._option_name_to_regex_group_id()
        for k in template.keys():
            template[k] = None
        return template

    def get_batch_request(
        self, options: Optional[BatchRequestOptions] = None
    ) -> BatchRequest:
        # All regex values passed to options must be strings to be used in the regex
        option_names_to_group = self._option_name_to_regex_group_id()
        if options:
            for option, value in options.items():
                if option in option_names_to_group and not isinstance(value, str):
                    raise ge_exceptions.InvalidBatchRequestError(
                        f"All regex matching options must be strings. The value of '{option}' is "
                        f"not a string: {value}"
                    )
        return super().get_batch_request(options)

    def _option_name_to_regex_group_id(self) -> BatchRequestOptions:
        option_to_group: BatchRequestOptions = dict(self.regex.groupindex)
        named_groups = set(self.regex.groupindex.values())
        for i in range(1, self.regex.groups + 1):
            if i not in named_groups:
                option_to_group[f"{self._unnamed_regex_param_prefix}{i}"] = i
        return option_to_group

    def get_batch_list_from_batch_request(
        self, batch_request: BatchRequest
    ) -> List[Batch]:
        self._validate_batch_request(batch_request)
        batch_list: List[Batch] = []

        for request, path in self._fully_specified_batch_requests_with_path(
            batch_request
        ):
            batch_spec = PathBatchSpec(path=str(path))
            data, markers = self.datasource.execution_engine.get_batch_data_and_markers(
                batch_spec=batch_spec
            )

            # batch_definition (along with batch_spec and markers) is only here to satisfy a
            # legacy constraint when computing usage statistics in a validator. We hope to remove
            # it in the future.
            # imports are done inline to prevent a circular dependency with core/batch.py
            from great_expectations.core import IDDict
            from great_expectations.core.batch import BatchDefinition

            batch_definition = BatchDefinition(
                datasource_name=self.datasource.name,
                data_connector_name="experimental",
                data_asset_name=self.name,
                batch_identifiers=IDDict(request.options),
                batch_spec_passthrough=None,
            )

            batch_metadata = copy.deepcopy(request.options)
            batch_metadata["path"] = path

            # Some pydantic annotations are postponed due to circular imports. This will set the annotations before we
            # instantiate the Batch class since we can import them above.
            Batch.update_forward_refs()
            batch_list.append(
                Batch(
                    datasource=self.datasource,
                    data_asset=self,
                    batch_request=request,
                    data=data,
                    metadata=batch_metadata,
                    legacy_batch_markers=markers,
                    legacy_batch_spec=batch_spec,
                    legacy_batch_definition=batch_definition,
                )
            )

        self.sort_batches(batch_list)

        return batch_list
