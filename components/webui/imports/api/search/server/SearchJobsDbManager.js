import msgpack from "@msgpack/msgpack";

import {sleep} from "../../../utils/misc";
import {
    JOB_STATUS_WAITING_STATES,
    SEARCH_JOB_STATUS,
} from "../constants";


const SEARCH_JOBS_TABLE_COLUMN_NAMES = {
    ID: "id",
    STATUS: "status",
    SEARCH_CONFIG: "search_config",
};

/**
 * Class for submitting and monitoring search jobs in the database.
 */
class SearchJobsDbManager {
    #sqlDbConnPool;
    #searchJobsTableName;

    /**
     * @param {import("mysql2/promise").Pool} sqlDbConnPool
     * @param {object} tableNames
     * @param {string} tableNames.searchJobsTableName
     */
    constructor(sqlDbConnPool, {searchJobsTableName}) {
        this.#sqlDbConnPool = sqlDbConnPool;
        this.#searchJobsTableName = searchJobsTableName;
    }

    /**
     * Submits a query job to the database.
     * @param {Object} searchConfig The arguments for the query.
     * @returns {Promise<number>} The job's ID.
     * @throws {Error} on error.
     */
    async submitQuery(searchConfig) {
        const [queryInsertResults] = await this.#sqlDbConnPool.query(
            `INSERT INTO ${this.#searchJobsTableName}
                 (${SEARCH_JOBS_TABLE_COLUMN_NAMES.SEARCH_CONFIG})
             VALUES (?)`,
            [Buffer.from(msgpack.encode(searchConfig))],
        );
        return queryInsertResults.insertId;
    }

    /**
     * Submits a query cancellation request to the database.
     * @param {number} jobId ID of the job to cancel.
     * @returns {Promise<void>}
     * @throws {Error} on error.
     */
    async submitQueryCancellation(jobId) {
        await this.#sqlDbConnPool.query(
            `UPDATE ${this.#searchJobsTableName}
             SET ${SEARCH_JOBS_TABLE_COLUMN_NAMES.STATUS} = ${SEARCH_JOB_STATUS.CANCELLING}
             WHERE ${SEARCH_JOBS_TABLE_COLUMN_NAMES.ID} = ?`,
            jobId,
        );
    }

    /**
     * Waits for the job to complete.
     * @param {number} jobId
     * @returns {Promise<void>}
     * @throws {Error} on MySQL error, if the job wasn't found in the database, if the job was
     * cancelled, or if the job completed in an unexpected state.
     */
    async awaitJobCompletion(jobId) {
        while (true) {
            let rows;
            try {
                const [queryRows, _] = await this.#sqlDbConnPool.query(
                    `SELECT ${SEARCH_JOBS_TABLE_COLUMN_NAMES.STATUS}
                     FROM ${this.#searchJobsTableName}
                     WHERE ${SEARCH_JOBS_TABLE_COLUMN_NAMES.ID} = ?`,
                    jobId,
                );
                rows = queryRows;
            } catch (e) {
                throw new Error(`Failed to query status for job ${jobId} - ${e}`);
            }
            if (rows.length < 1) {
                throw new Error(`Job ${jobId} not found in database.`);
            }
            const status = rows[0][SEARCH_JOBS_TABLE_COLUMN_NAMES.STATUS];

            if (false === JOB_STATUS_WAITING_STATES.includes(status)) {
                if (SEARCH_JOB_STATUS.CANCELLED === status) {
                    throw new Error(`Job ${jobId} was cancelled.`);
                } else if (SEARCH_JOB_STATUS.SUCCEEDED !== status) {
                    throw new Error(`Job ${jobId} exited with unexpected status=${status}: `
                        + `${Object.keys(SEARCH_JOB_STATUS)[status]}.`);
                }
                break;
            }

            await sleep(0.5);
        }
    }
}

export default SearchJobsDbManager;
